"""Upload the two bulk files to the Hugging Face dataset.

    python tools/upload_bulk_data.py                 # both files, in order
    python tools/upload_bulk_data.py --only domains.txt
    python tools/upload_bulk_data.py --dataset someone/else --dry-run

The token is read from ~/.cache/huggingface/token (what `hf auth login` writes), or
from --token-file. It needs to be a WRITE token: a classic token with the `write` role,
or a fine-grained token with "write access to contents" **and** permission to create the
repository in its namespace.

The files are streamed from where they are - nothing is copied first - so a 7.8 GB file
costs no extra disk. Uploading is resumable at file granularity: run it again and the
unchanged file is skipped.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET = "Jurajbrando/lead-studio-data"
FILES = (
    # (name in the dataset, local path)
    ("rec_results.duckdb", os.path.join(ROOT, "imports", "rec_results.duckdb")),
)


def read_token(path=None):
    path = path or os.path.expanduser("~/.cache/huggingface/token")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def human(size):
    return "%.2f GB" % (size / 1073741824.0) if size >= 1073741824 else "%.0f MB" % (size / 1048576.0)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Upload the bulk data files to Hugging Face.")
    parser.add_argument("--dataset", default=DATASET, help="dataset repo id (default: %s)" % DATASET)
    parser.add_argument("--token-file", default="", help="default: ~/.cache/huggingface/token")
    parser.add_argument("--only", default="", help="one file name, e.g. domains.txt")
    parser.add_argument("--dry-run", action="store_true", help="show what would go, upload nothing")
    args = parser.parse_args(argv)

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("This needs huggingface_hub: pip install huggingface_hub")
        return 2
    token = read_token(args.token_file)
    if not token:
        print("No token at %s - run `hf auth login` first (a WRITE token)."
              % (args.token_file or "~/.cache/huggingface/token"))
        return 2

    api = HfApi(token=token)
    try:
        who = api.whoami()
        print("Signed in as %s" % who.get("name"))
    except Exception as exc:                        # noqa: BLE001
        print("That token was rejected: %s" % str(exc)[:200])
        return 2

    wanted = [entry for entry in FILES if not args.only or entry[0] == args.only]
    if not wanted:
        print("Nothing matches --only %s (files: %s)"
              % (args.only, ", ".join(name for name, _ in FILES)))
        return 2
    missing = [path for _, path in wanted if not os.path.isfile(path)]
    if missing:
        print("Not found:\n  " + "\n  ".join(missing))
        return 2
    for name, path in wanted:
        print("  %-22s %-10s %s" % (name, human(os.path.getsize(path)), path))
    if args.dry_run:
        print("Dry run - nothing uploaded.")
        return 0

    if not args.dry_run:
        try:
            url = api.create_repo(args.dataset, repo_type="dataset", private=False, exist_ok=True)
            print("Dataset: %s" % url)
        except Exception as exc:                    # noqa: BLE001
            text = str(exc)
            print("Could not open the dataset repo: %s" % text[:300])
            if "403" in text or "rights" in text.lower():
                print("The token can read but not create/write in that namespace. Use a classic "
                      "token with the write role, or a fine-grained token with write access to "
                      "contents and the permission to create repositories.")
            return 3

    for name, path in wanted:
        print("\nUploading %s (%s)..." % (name, human(os.path.getsize(path))))
        try:
            api.upload_file(path_or_fileobj=path, path_in_repo=name, repo_id=args.dataset,
                            repo_type="dataset")
        except Exception as exc:                    # noqa: BLE001
            print("  failed: %s" % str(exc)[:300])
            return 3
        print("  done: https://huggingface.co/datasets/%s/resolve/main/%s" % (args.dataset, name))

    print("\nBoth files are on the Hub.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
