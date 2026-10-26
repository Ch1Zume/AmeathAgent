"""Stage a pinned upstream release. Does not install into or launch the game."""
from pathlib import Path
import hashlib
import io
import json
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
URL = "https://github.com/CharTyr/STS2-Agent/releases/download/v0.16.2/sts2-ai-agent-v0.16.2-windows.zip"
EXPECTED = "97eff7ff6616145dd80103ee5b4f41ec62ad5b519cac3ad9fa83014dbe786a3c"
FILES = ("STS2AIAgent.dll", "STS2AIAgent.pck", "mod_id.json")


def main():
    request = urllib.request.Request(URL, headers={"User-Agent": "Ameath/0.2"})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(10_000_001)
    if len(data) > 10_000_000 or hashlib.sha256(data).hexdigest() != EXPECTED:
        raise RuntimeError("Bridge release checksum mismatch; no files were installed.")
    folder = ROOT / "integrations" / "sts2" / "mod"
    folder.mkdir(parents=True, exist_ok=True)
    hashes = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in FILES:
            entries = [entry for entry in archive.infolist()
                       if Path(entry.filename).name == name and "/mod/" in "/" + entry.filename.replace("\\", "/")]
            if len(entries) != 1 or entries[0].file_size > 30_000_000:
                raise RuntimeError("Unexpected bridge release layout.")
            content = archive.read(entries[0])
            (folder / name).write_bytes(content)
            hashes[name] = hashlib.sha256(content).hexdigest()
        licenses = [entry for entry in archive.infolist() if Path(entry.filename).name.upper() in ("LICENSE", "LICENSE.TXT", "COPYING")]
        if licenses:
            (folder.parent / "UPSTREAM-LICENSE.txt").write_bytes(archive.read(licenses[0]))
    provenance = {"project": "https://github.com/CharTyr/STS2-Agent", "version": "v0.16.2",
                  "license": "AGPL-3.0", "source": "https://github.com/CharTyr/STS2-Agent/tree/v0.16.2",
                  "release_url": URL, "release_sha256": EXPECTED, "files": hashes}
    (folder.parent / "bridge-release.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(json.dumps({"staged": str(folder), "version": "v0.16.2", "verified": True, "files": list(hashes)}))


if __name__ == "__main__":
    main()
