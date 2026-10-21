"""One bounded parsing child. This runs only inside the no-secret parser service."""

import base64
import json
import os
import resource
import sys


def main():
    # Bounds apply to this process and Tesseract descendants, not the SQL worker.
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024**2, 16 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.environ["OMP_THREAD_LIMIT"] = "1"
    payload = json.loads(sys.stdin.buffer.read(8 * 1024**2))
    content = base64.b64decode(payload["content"], validate=True)
    if not content or len(content) > 5 * 1024**2:
        raise ValueError("Source exceeds parser input boundary")
    if payload["operation"] == "boundary-probe":
        print(
            json.dumps(
                {
                    "uid": os.getuid(),
                    "address_space": resource.getrlimit(resource.RLIMIT_AS),
                    "cpu": resource.getrlimit(resource.RLIMIT_CPU),
                    "open_files": resource.getrlimit(resource.RLIMIT_NOFILE),
                    "file_size": resource.getrlimit(resource.RLIMIT_FSIZE),
                    "core": resource.getrlimit(resource.RLIMIT_CORE),
                    "omp_thread_limit": os.environ["OMP_THREAD_LIMIT"],
                }
            )
        )
        return
    from .config import Settings
    from .extraction import extract_text, preview_pdf, PermanentExtractionError, TransientProviderError

    try:
        settings = Settings(parser_url="")
        if payload["operation"] == "extract":
            text, used_ocr = extract_text(content, payload["content_type"], settings)
            result = {"text": text, "used_ocr": used_ocr}
        elif payload["operation"] == "preview":
            result = {"png": base64.b64encode(preview_pdf(content, settings)).decode()}
        else:
            raise PermanentExtractionError("Unsupported operation")
    except PermanentExtractionError:
        result = {"error": "permanent"}
    except TransientProviderError:
        result = {"error": "transient"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
