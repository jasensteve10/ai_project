"""Stage the Docker build context and upload it to a Hugging Face Space (public demo, no API keys).

    hf auth login                                   # once; or export HF_TOKEN
    python deploy/huggingface/deploy_space.py --space <user>/<space-name> [--private] [--dry-run]

The Space builds the repository Dockerfile. PUBLIC_DEMO=1 is set as a Space variable
(and is the image default), so no model client is ever created; never add API keys
as Space secrets for this demo.
"""
import argparse
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INCLUDE = ['Dockerfile', '.dockerignore', 'requirements-frozen.txt', 'requirements.lock', 'pytest.ini',
           'app', 'src', 'configs', 'dataset', 'benchmarks', 'experiments', 'tests',
           'docs/eda/tables/data_dictionary.csv']  # read at runtime by the retrieval corpus
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', '~$*', 'embeddings', '.env*')


def stage(dest):
    for name in INCLUDE:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=IGNORE)
        else:
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
    shutil.copy2(Path(__file__).with_name('SPACE_README.md'), dest / 'README.md')
    leaked = [p for p in dest.rglob('*') if p.name == '.env' or p.name.startswith('.env.')]
    if leaked:
        raise SystemExit(f'Refusing to upload environment files: {leaked}')
    return sum(p.stat().st_size for p in dest.rglob('*') if p.is_file())


def stage_to(path):
    """Stage into ``path`` (for a local ``docker build`` of exactly what would be uploaded)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    return stage(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--space', required=True, help='<user or org>/<space name>')
    parser.add_argument('--private', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='stage and list files without uploading')
    parser.add_argument('--stage-dir', type=Path, help='only stage into this new directory (test with docker build)')
    args = parser.parse_args()
    if args.stage_dir:
        print(f'{stage_to(args.stage_dir) / 1e6:.1f} MB staged in {args.stage_dir}')
        return
    with tempfile.TemporaryDirectory(prefix='hf-space-') as tmp:
        dest = Path(tmp)
        size = stage(dest)
        files = sorted(str(p.relative_to(dest)) for p in dest.rglob('*') if p.is_file())
        print(f'{len(files)} files, {size / 1e6:.1f} MB staged')
        if args.dry_run:
            print('\n'.join(files))
            return
        from huggingface_hub import HfApi
        api = HfApi()
        print(f"Uploading as {api.whoami()['name']} to https://huggingface.co/spaces/{args.space}")
        api.create_repo(args.space, repo_type='space', space_sdk='docker', private=args.private, exist_ok=True)
        api.add_space_variable(args.space, 'PUBLIC_DEMO', '1')
        api.upload_folder(repo_id=args.space, repo_type='space', folder_path=str(dest),
                          commit_message='Deploy public demo (no model calls)')
        print(f'Done. Build logs: https://huggingface.co/spaces/{args.space}?logs=build')


if __name__ == '__main__':
    main()
