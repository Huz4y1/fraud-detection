import shutil
import urllib.request

from fraud.config import LANDING, SOURCE_FILES, SOURCE_URL


def main():
    LANDING.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_FILES:
        target = LANDING / name
        if target.exists():
            print(f"{name} already in landing zone")
            continue
        partial = target.with_suffix(".part")
        with urllib.request.urlopen(f"{SOURCE_URL}/{name}") as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
        partial.rename(target)
        print(f"downloaded {name} ({target.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
