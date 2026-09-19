"""Regenerate the runtime inventory from actual plugin files; no quantity data."""
from pathlib import Path
import hashlib
import json


def main():
    root=Path(__file__).resolve().parents[1]
    files=[]
    for path in sorted((root/'skills').rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix in {'.pyc','.pyo'}:
            continue
        data=path.read_bytes()
        files.append({'path':path.relative_to(root/'skills').as_posix(),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
    document={'schema_version':'cad-qto-runtime-manifest/1.0','professional_skills':13,'coordinator_skills':1,'runtime_file_count':len(files),'files':files}
    (root/'运行文件清单.json').write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'status':'PASS','runtime_file_count':len(files)}))


if __name__=='__main__':main()
