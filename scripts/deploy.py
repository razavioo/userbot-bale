#!/usr/bin/env python3
"""baleobala declarative deployer.

Reads deploy/inventory.yaml and syncs code + runs install scripts on each node.

Usage:
    python3 scripts/deploy.py                    # deploy all nodes
    python3 scripts/deploy.py --node relay-ir-1  # deploy one node
    python3 scripts/deploy.py --dry-run          # print commands without running
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pyyaml is required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY = REPO_ROOT / "deploy" / "inventory.yaml"

RSYNC_EXCLUDES = [
    "--exclude=.git",
    "--exclude=__pycache__",
    "--exclude=*.pyc",
    "--exclude=.venv",
    "--exclude=native",
    "--exclude=node_modules",
    "--exclude=build",
    "--exclude=dist",
    "--exclude=*.egg-info",
]


def _run(cmd: list[str], dry_run: bool) -> None:
    print("  $", " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)


def _deploy_node(node: dict, dry_run: bool) -> None:
    name = node.get("name", node.get("role", "unknown"))
    host = node["host"]
    user = node.get("user", "root")
    remote = node["remote_path"]
    target = f"{user}@{host}"

    print(f"\n==> [{name}] syncing code to {target}:{remote}")
    _run(
        ["rsync", "-avz", "--delete", *RSYNC_EXCLUDES, str(REPO_ROOT) + "/", f"{target}:{remote}/"],
        dry_run,
    )

    print(f"==> [{name}] reinstalling package")
    _run(["ssh", target, f"cd {remote} && .venv/bin/pip install -e . -q"], dry_run)

    install_script = node.get("install_script")
    if install_script:
        print(f"==> [{name}] running {install_script}")
        _run(["ssh", target, f"bash {remote}/{install_script}"], dry_run)

    print(f"==> [{name}] done")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy baleobala nodes from inventory.yaml")
    parser.add_argument("--node", help="Deploy only this named node (matches name or role)")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    parser.add_argument("--inventory", default=str(INVENTORY), help="Path to inventory YAML")
    args = parser.parse_args()

    inventory_path = Path(args.inventory)
    if not inventory_path.exists():
        print(f"Inventory not found: {inventory_path}", file=sys.stderr)
        sys.exit(1)

    with inventory_path.open() as f:
        inv = yaml.safe_load(f)

    nodes: list[dict] = list(inv.get("relays", []))

    if args.node:
        nodes = [n for n in nodes if n.get("name") == args.node or n.get("role") == args.node]
        if not nodes:
            print(f"No node matching --node={args.node!r} in inventory", file=sys.stderr)
            sys.exit(1)

    if args.dry_run:
        print("[dry-run] no changes will be made")

    for node in nodes:
        _deploy_node(node, dry_run=args.dry_run)

    print("\nDeployment complete.")


if __name__ == "__main__":
    main()
