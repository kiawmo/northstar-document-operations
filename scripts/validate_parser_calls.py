"""Observe XML entry points for supported synthetic documents in a secret-free container."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


PROBE = r'''
import ctypes,json
from pathlib import Path
from docops.config import Settings
from docops.extraction import extract_text,preview_pdf
log=Path('/tmp/xml-calls')
cases=[]
for name,mime in [('valid.txt','text/plain'),('valid.pdf','application/pdf'),('scanned.pdf','application/pdf'),('scanned.png','image/png')]:
    log.unlink(missing_ok=True)
    text,ocr=extract_text((Path('/sources')/name).read_bytes(),mime,Settings(parser_url='',parser_socket=''))
    calls=log.read_text().splitlines() if log.exists() else []
    assert text.strip()
    assert not any(' xml' in call or 'g_dbus_' in call for call in calls),calls
    cases.append({'source':name,'used_ocr':ocr,'xml_or_dbus_calls':0,'file_calls':[line.split(' ',2)[1:] for line in calls]})
log.unlink(missing_ok=True)
assert preview_pdf(Path('/sources/valid.pdf').read_bytes(),Settings(parser_url='',parser_socket='')).startswith(b'\x89PNG')
assert not log.exists() or not any(' xml' in line for line in log.read_text().splitlines())
ctypes.CDLL('libxml2.so.2',mode=ctypes.RTLD_GLOBAL)
entry=ctypes.CDLL(None).xmlReadMemory
entry.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int]
entry.restype=ctypes.c_void_p
assert entry(b'<root/>',7,None,None,0)
positive=log.read_text()
assert 'xmlReadMemory' in positive
print(json.dumps({'result':'passed','supported_cases':cases,'pdf_preview':'passed','xml_hook_positive_control':'passed'}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-library", type=Path, required=True, help="Linux .so compiled from the included C diagnostic")
    parser.add_argument("--image", default="northstar-docops:local")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    library = args.audit_library.resolve(strict=True)
    image = subprocess.check_output(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True).strip()
    command = [
        "docker", "run", "--rm", "-i", "--init", "--network", "none", "--read-only", "--user", "10001:10001",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--memory", "1g", "--pids-limit", "32",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m,uid=10001,gid=10001",
        "--mount", f"type=bind,source={library},target=/opt/call-audit.so,readonly",
        "--mount", f"type=bind,source={root / 'fixtures'},target=/sources,readonly",
        "-e", "LD_PRELOAD=/opt/call-audit.so", "-e", "DOCUMENT_XML_CALL_LOG=/tmp/xml-calls",
        image, "python", "-",
    ]
    result = subprocess.run(command, input=PROBE, text=True, capture_output=True, timeout=180, check=True)
    data = json.loads(result.stdout)
    data["image_reference"] = image
    data["diagnostic_source_sha256"] = hashlib.sha256((root / "containers/diagnostics/libxml_call_audit.c").read_bytes()).hexdigest()
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
