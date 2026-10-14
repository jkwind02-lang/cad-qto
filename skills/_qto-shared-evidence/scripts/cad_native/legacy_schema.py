"""以已安装 IExtDbObject.grx 的读取调用为依据的受限类型解析。

支持声明保守：只读版本化字段，未核明的对象几何语义保留为候选。
模块SHA及函数RVA见SCHEMA_EVIDENCE。任何未知分支停止该对象，不扫描猜偏移。
"""
import math
import re
from .bitstream import BitStream, ParseError

SCHEMA_EVIDENCE = {
    "module_sha256": "66ffd0c2185c01dc264c4345cd48839d2908b462347e53dfb7d7f547eea22572",
    "base_reader_rva": "0x1c4f0", "datamap_reader_rva": "0x642c0",
    "interfaces_reader_rva": "0x30210", "pipe_reader_rva": "0x305c0",
    "fitting_reader_rva": "0x30d40", "equipment_base_reader_rva": "0x31260",
    "context17_string_reader_rva": "0x21820", "context_selector_rva": "0x211b0",
    "extents_edge_reader_rva":"0x2fa30", "fitting_mydata_reader_rva":"0x30b90",
    "legacy_context16_constant_rva":"0x2177e",
    "geometry_semantics_verified": False,
}
SUPPORTED_CLASSES = {"TCH_DBHVACDUCT", "TCH_DBHFITTING", "TCH_DBHEQUIPMENT", "TCH_HVACDIMENSION"}


class NativeReader:
    def __init__(self, numeric, string, numeric_bits, string_bits, references=(), legacy_inline=False, legacy_encoding="cp936"):
        self.d = BitStream(numeric, numeric_bits, "marker161")
        self.legacy_inline = legacy_inline
        self.s = self.d if legacy_inline else BitStream(string, string_bits, "marker162")
        self.text = (lambda: self.d.tv(legacy_encoding)) if legacy_inline else self.s.tu
        self.trace = []
        self.result = {"schema_evidence": SCHEMA_EVIDENCE, "fields": {}, "named_properties": {}, "geometry_candidate": None,
                       "geometry_verified": False, "usable_for_quantity": False}
        self.result["stream_profile"] = f"TV-inline-base9-context16-{legacy_encoding}" if legacy_inline else "TU-separated-base10-context17"
        self.guid = ""
        self.references = list(references)
        self.reference_pos = 0

    def object_id(self):
        # Proxy object IDs are held in the separate ordered DXF 330/340/350/360 table.
        # They do not consume the numeric retained-data stream.
        if self.reference_pos >= len(self.references):
            raise ParseError("proxy object-ID table is truncated", self.d)
        value = dict(self.references[self.reference_pos])
        value["reference_table_index"] = self.reference_pos
        self.reference_pos += 1
        return value

    def field(self, name, read):
        before = [self.d.pos, self.s.pos, self.reference_pos]
        value = read()
        self.trace.append({"field": name, "value": value, "numeric_bits": [before[0], self.d.pos], "string_bits": [before[1], self.s.pos], "reference_indices": [before[2], self.reference_pos]})
        self.result["fields"][name] = value
        return value

    def require(self, condition, message):
        if not condition:
            raise ParseError(message, self.d)

    def encoded(self, kind, context, identifier=None):
        if context == 0:
            return {"byte": self.d.rc, "short": self.d.bs, "int": self.d.bl, "double": self.d.bd}[kind]()
        self.require(context in (16, 17), f"unsupported scalar context {context}")
        raw = self.text()
        self.require(len(raw) % 2 == 0 and len(raw) >= 2, "invalid encoded scalar string length")
        guid = self.guid if identifier is None else identifier
        digit = lambda index: int(guid[index]) if index < len(guid) and guid[index] in "0123456789" else 0
        seed = (100*digit(4)+10*digit(16)+digit(30)) & 255 if len(guid) > 35 else 0
        key = 65 if context == 16 else (198 if seed > 99 else 223)
        xor = 5 if context == 16 else 9
        text = "".join(chr(((26*ord(raw[i])-27*key+ord(raw[i+1])) & 65535) ^ xor) for i in range(0, len(raw), 2))
        # Static reader removes index 0, then index 1 of the remaining character list.
        if context >= 17:
            text = text[1:]
            self.require(len(text) >= 2, "context17 scalar lacks two padding characters")
            text = text[:1]+text[2:]
        self.require(bool(re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text)), "decoded scalar is not an exact numeric token")
        value = float(text) if kind == "double" else int(text)
        if kind == "double":
            self.require(math.isfinite(value), "decoded scalar is non-finite")
        if kind == "int":
            self.require(-(2**31) <= value < 2**31, "encoded integer outside signed 32-bit width")
        if kind in ("byte", "short"):
            self.require(0 <= value <= (255 if kind == "byte" else 65535), "encoded integer outside declared width")
        return value

    def datamap(self):
        self.require(self.text() == "TDBXDATAMAP_BLOCK_BEGIN_", "property map begin marker mismatch")
        properties = []
        self.result["property_entries"] = properties
        for _ in range(4096):
            name = self.text()
            if name == "TDBXDATAMAP_BLOCK_END_":
                self.result["named_properties"] = {item["name"]: item["value"] for item in properties}
                self.result["property_entries"] = properties
                return properties
            self.require(bool(name) and name.isprintable(), "invalid property name")
            self.require(name not in {item["name"] for item in properties}, "duplicate property name")
            before = [self.d.pos, self.s.pos]
            tag = self.d.bl()
            self.result["property_in_progress"] = {"name": name, "type_tag": tag, "numeric_start": before[0], "string_value_start": before[1]}
            readers = {3: self.d.bl, 5: self.d.bd, 8: self.text,
                       0x4005: self.d.point3, 0x4006: self.d.point3,
                       0x4007: self.d.point3, 0x4008: self.d.point3, 0x4009: self.text,
                       19: self.object_id, 21: self.object_id}
            self.require(tag in readers, f"unsupported named-property type {tag}")
            value = readers[tag]()
            if tag in (19, 21):
                self.require(value["group_code"] == (330 if tag == 19 else 340), "object-ID reference kind does not match property type")
            properties.append({"name": name, "type_tag": tag, "value": value,
                               "numeric_bits": [before[0], self.d.pos], "value_string_bits": [before[1], self.s.pos]})
            self.result.pop("property_in_progress", None)
        raise ParseError("property map exceeds entry bound", self.s)

    def base(self):
        version = self.field("entity_version", self.d.rc)
        self.require(version in (9, 10), f"unsupported base version {version}")
        context = self.field("entity_context", self.d.rc)
        self.require((version, context) in ((9, 16), (10, 17)), f"unsupported entity version/context {version}/{context}")
        if version >= 10:
            self.guid = self.field("entity_format_identifier", self.text)
        self.field("entity_scalar_0x10", lambda: self.encoded("double", context))
        self.field("entity_scalar_0x18", lambda: self.encoded("double", context))
        flags = self.field("entity_reference_flags", lambda: self.encoded("byte", context))
        self.require(flags & ~3 == 0, "unknown entity reference flag")
        for mask, adjustment, name in ((1, 5, "first"), (2, 3, "second")):
            if flags & mask:
                count = self.field(f"{name}_reference_count", lambda: self.encoded("byte", context))-adjustment
                self.require(0 <= count <= 64, "reference count outside bounded range")
                for index in range(count):
                    self.field(f"{name}_reference_{index}", self.object_id)
        fields_count = self.field("entity_fields_count", self.d.bl)
        self.require(fields_count == 0, "nonempty TEntityFields table not yet supported")
        self.field("entity_property_map", self.datamap)
        self.field("entity_tail_string", self.text)
        if version > 9:
            self.field("entity_tail_byte", lambda: self.encoded("byte", context))

    def interfaces(self):
        version = self.field("interfaces_version", self.d.bs)
        self.require(version in (2, 3), f"unsupported interface version {version}")
        context = self.field("interfaces_context", self.d.rc)
        self.require(context in (0, 16, 17), f"unsupported interface context {context}")
        self.field("interface_reference_1", self.object_id)
        self.field("interface_reference_2", self.object_id)
        if version > 2:
            self.field("interfaces_flag", lambda: self.encoded("byte", context))
            self.field("interfaces_short", lambda: self.encoded("short", context))

    def pipe(self):
        version = self.field("pipe_version", self.d.bs)
        self.require(version in (3, 4), f"unsupported pipe version {version}")
        context = self.field("pipe_context", self.d.rc)
        self.require(context in (0, 16, 17), f"unsupported pipe context {context}")
        count = self.field("pipe_extents_edge_count", lambda: self.encoded("int", context))
        self.read_edges(count, "pipe")
        refs = self.field("pipe_reference_count", lambda: self.encoded("int", context))
        self.require(0 <= refs <= 256, "pipe reference count out of bounds")
        for index in range(refs):
            self.field(f"pipe_reference_{index}", self.object_id)
        if version == 4:
            def point():
                raw = self.d.point3()
                return [raw[2], raw[0], raw[1]] if context >= 17 else raw
            p1 = self.field("pipe_point_0x148", point)
            p2 = self.field("pipe_point_0x160", point)
            self.field("pipe_vector_0x178", point)
            self.field("pipe_double_0x190", lambda: self.encoded("double", context))
            self.field("pipe_short_0x198", lambda: self.encoded("short", context))
            self.result["geometry_candidate"] = {"kind": "serialized_pipe_point_pair", "local_points": [p1, p2],
                "meaning": "typed point fields from TDbPipeBase; centerline semantics awaiting independent evidence",
                "quantity_allowed": False}

    def fitting(self):
        version = self.field("fitting_version", self.d.bs)
        self.require(version in (2, 3), f"unsupported fitting version {version}")
        context = self.field("fitting_context", self.d.rc) if version >= 3 else 0
        self.field("fitting_reference", self.object_id)
        nested_context = self.field("fitting_mydata_context", self.d.rc) if version >= 3 else 0
        count = self.field("fitting_extents_edge_count", lambda: self.encoded("int", nested_context, ""))
        self.read_edges(count, "fitting")
        refs = self.field("fitting_reference_count", lambda: self.encoded("int", nested_context, ""))
        self.require(0 <= refs <= 256, "fitting reference count out of bounds")
        for index in range(refs):
            self.field(f"fitting_geometry_reference_{index}", self.object_id)
        derived = self.field("hvac_fitting_version", self.d.bs)
        self.require(0 <= derived <= 2, f"unsupported HVAC fitting version {derived}")

    def read_edges(self, count, label):
        self.require(0 <= count <= 4096, "extents edge count outside bound")
        edges = []
        for index in range(count):
            owner = self.field(f"{label}_edge_{index}_owner", self.object_id)
            elements = self.field(f"{label}_edge_{index}_count", self.d.bl)
            self.require(0 <= elements <= 4096, "curve element count outside bound")
            curves = []
            for j in range(elements):
                def element():
                    return {"points": [self.d.point3(), self.d.point3(), self.d.point3()],
                            "kind_code": self.d.bs(), "graphics_marker": self.d.bl(), "parameter": self.d.bd()}
                curves.append(self.field(f"{label}_edge_{index}_curve_{j}", element))
            edges.append({"owner": owner, "curves": curves})
        self.result["serialized_extents_edges"] = edges

    def equipment(self):
        version = self.field("part_version", self.d.bs)
        self.require(0 <= version <= 3, f"unsupported part version {version}")
        context = self.field("part_context", self.d.rc) if version >= 3 else 0
        self.require(context in (0, 16, 17), f"unsupported part context {context}")
        self.field("part_reference", self.object_id)
        self.require(version != 1, "part version1 extra map not yet supported")
        derived = self.field("hvac_equipment_version", self.d.bs)
        self.require(derived in (0, 1), f"unsupported equipment derived version {derived}")

    def parse(self, class_name):
        try:
            self.require(class_name in SUPPORTED_CLASSES, "unsupported native class")
            self.base()
            self.result["base_complete"] = True
            if class_name == "TCH_HVACDIMENSION":
                raise ParseError("HVAC dimension derived schema not yet supported", self.d)
            self.interfaces()
            if class_name == "TCH_DBHVACDUCT":
                self.pipe()
                version = self.field("hvac_duct_version", self.d.bs)
                self.require(version in (0, 1, 2), f"HVAC duct derived version {version} needs additional fields")
                self.result["status"] = "SERIALIZATION_COMPLETE"
            elif class_name == "TCH_DBHFITTING":
                self.fitting()
                self.result["status"] = "SERIALIZATION_COMPLETE"
            else:
                self.equipment()
                self.result["status"] = "SERIALIZATION_COMPLETE"
        except (ParseError, ValueError, OverflowError) as error:
            self.result["status"] = "PARTIAL_UNSUPPORTED" if self.result["fields"] else "UNSUPPORTED"
            self.result["stopped_at"] = {"reason": str(error), "stream": getattr(error, "stream", None), "bit": getattr(error, "bit", None)}
        self.result["trace"] = self.trace
        self.result["consumed"] = [self.d.position()] if self.legacy_inline else [self.d.position(), self.s.position()]
        self.result["consumed_references"] = {"used": self.reference_pos, "available": len(self.references)}
        if self.result["status"] == "SERIALIZATION_COMPLETE" and (self.d.pos != self.d.limit or self.s.pos != self.s.limit or self.reference_pos != len(self.references)):
            self.result["status"] = "PARTIAL_UNCONSUMED_TAIL"
        entries = self.result.get("property_entries", [])
        if self.result.get("base_complete"):
            points = {item["name"]: item["value"] for item in entries if item["type_tag"] in (0x4005, 0x4006)}
            vectors = {item["name"]: item["value"] for item in entries if item["type_tag"] in (0x4007, 0x4008)}
            self.result["geometry_candidate"] = {"kind": "native_named_point_fields", "points_local": points, "vectors_local": vectors,
                "endpoint_pair_local": [points["Start"], points["End"]] if "Start" in points and "End" in points else None,
                "meaning": "native typed named fields; complete curves and engineering measurement remain unverified", "quantity_allowed": False}
        self.result["properties_complete"] = bool(self.result.get("base_complete"))
        return self.result


def parse_native(class_name, sections, references=(), legacy_encoding="cp936"):
    if 161 not in sections:
        return {"status": "UNSUPPORTED_CONTAINER", "reason": "numeric stream is required", "usable_for_quantity": False}
    numeric = sections[161]
    inline = 162 not in sections
    if inline and numeric["data"][:2] != b"\x09\x10":
        return {"status": "UNSUPPORTED_CONTAINER", "reason": "unsupported inline stream profile", "usable_for_quantity": False}
    string = sections.get(162, {"data": b"", "declared": 0})
    try:
        return NativeReader(numeric["data"], string["data"], numeric["declared"], string["declared"], references, inline, legacy_encoding).parse(class_name)
    except ParseError as error:
        return {"status": "INVALID_CONTAINER", "reason": str(error), "usable_for_quantity": False}
