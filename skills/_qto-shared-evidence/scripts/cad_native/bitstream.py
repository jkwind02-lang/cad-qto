import math
import struct


class ParseError(ValueError):
    def __init__(self, message, stream=None):
        self.stream = stream.name if stream else None
        self.bit = stream.pos if stream else None
        super().__init__(message)


class BitStream:
    def __init__(self, data, bit_count=None, name="data"):
        self.data = bytes(data)
        self.limit = len(data)*8 if bit_count is None else int(bit_count)
        self.pos = 0
        self.name = name
        if self.limit < 0 or self.limit > len(data)*8:
            raise ParseError("declared bit count exceeds physical bytes", self)

    def uint(self, count):
        if count < 0 or self.pos+count > self.limit:
            raise ParseError(f"truncated {count}-bit read", self)
        if count == 0:
            return 0
        start, offset = divmod(self.pos, 8)
        size = (offset+count+7)//8
        value = int.from_bytes(self.data[start:start+size], "big")
        value = (value >> (size*8-offset-count)) & ((1 << count)-1)
        self.pos += count
        return value

    def octets(self, count):
        return bytes(self.uint(8) for _ in range(count))

    def rc(self):
        return self.uint(8)

    def bs(self):
        flag = self.uint(2)
        if flag == 0:
            return int.from_bytes(self.octets(2), "little")
        if flag == 1:
            return self.rc()
        return 0 if flag == 2 else 256

    def bl(self):
        flag = self.uint(2)
        if flag == 0:
            return int.from_bytes(self.octets(4), "little", signed=True)
        if flag == 1:
            return self.rc()
        if flag == 2:
            return 0
        raise ParseError("reserved bit-long selector", self)

    def bd(self):
        flag = self.uint(2)
        if flag == 0:
            value = struct.unpack("<d", self.octets(8))[0]
        elif flag in (1, 2):
            value = 1.0 if flag == 1 else 0.0
        else:
            raise ParseError("reserved bit-double selector", self)
        if not math.isfinite(value):
            raise ParseError("non-finite double", self)
        return value

    def point3(self):
        return [self.bd(), self.bd(), self.bd()]

    def tu(self, max_chars=65536):
        count = self.bs()
        if count > max_chars:
            raise ParseError("string exceeds bounded character count", self)
        try:
            return self.octets(count*2).decode("utf-16le", errors="strict")
        except UnicodeDecodeError as error:
            raise ParseError("invalid UTF-16 string", self) from error

    def tv(self, encoding="cp936"):
        count = self.bs()
        data = self.octets(count)
        if not data.endswith(b"\0"):
            raise ParseError("legacy TV string lacks terminal NUL", self)
        try:
            return data[:-1].decode(encoding, errors="strict")
        except UnicodeDecodeError as error:
            raise ParseError("invalid legacy code-page string", self) from error

    def handle(self):
        header = self.rc()
        code, count = header >> 4, header & 15
        if count > 8:
            raise ParseError("handle width exceeds 64 bits", self)
        return {"code": code, "value": int.from_bytes(self.octets(count), "big"), "encoding": "DWG handle candidate"}

    def position(self):
        return {"stream": self.name, "bit": self.pos, "declared_bits": self.limit}
