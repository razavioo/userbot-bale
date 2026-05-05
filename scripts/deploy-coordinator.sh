#!/usr/bin/env bash
# Deploy coordinator + mesh exit-node to production server.
# Run from your LOCAL machine: bash scripts/deploy-coordinator.sh
# Requirements: ssh access to root@68.183.118.171, rsync, sshpass (optional)
set -euo pipefail

SERVER="root@68.183.118.171"
REMOTE="/opt/baleobala"
JWT_DIR="/root/.baleobala/accounts"
ETC_DIR="/etc/baleobala"

echo "==> Syncing code to $SERVER:$REMOTE"
rsync -avz --delete \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.venv' \
  --exclude 'native' \
  --exclude 'node_modules' \
  ./ "$SERVER:$REMOTE/"

echo "==> Reinstalling baleobala package on server"
ssh "$SERVER" "cd $REMOTE && .venv/bin/pip install -e . -q"

echo ""
echo "==> Probing peer_id for each JWT..."
ssh "$SERVER" python3 - <<'PYEOF'
import sys, json, pathlib, os

# Add project src to path
project = "/opt/baleobala"
sys.path.insert(0, f"{project}/src")
os.environ.setdefault("BALEOBALA_SECRET_BACKEND", "file")
os.environ.setdefault("BALEOBALA_HOME", "/root/.config/baleobala")

JWT_DIR = "/root/.baleobala/accounts"
results = {}

try:
    from baleobala.bale.api import BaleApiClient
    from baleobala.bale.ws_client import WsClient
except ImportError as e:
    print(f"Import error: {e}", file=sys.stderr)
    sys.exit(1)

jwt_files = sorted(pathlib.Path(JWT_DIR).glob("account-*.jwt"))
if not jwt_files:
    print(f"No JWT files found in {JWT_DIR}", file=sys.stderr)
    sys.exit(1)

print(f"Found {len(jwt_files)} JWT files", file=sys.stderr)

for jwt_path in jwt_files:
    jwt = jwt_path.read_text().strip()
    client = BaleApiClient(jwt=jwt)
    try:
        client.start(timeout=15)
        # The ws_client stores user_id from the init ack
        # Access it via the underlying ws client
        ws = client._ws
        user_id = getattr(ws, '_user_id', None) or getattr(ws, 'user_id', None)
        if user_id is None:
            # Try via auth info in JWT payload
            import base64
            parts = jwt.split('.')
            if len(parts) >= 2:
                payload = parts[1] + '=' * ((4 - len(parts[1]) % 4) % 4)
                data = json.loads(base64.urlsafe_b64decode(payload))
                user_id = data.get('sub') or data.get('user_id')
        results[jwt_path.name] = {"peer_id": user_id, "jwt_preview": jwt[:20] + "..."}
        print(f"  {jwt_path.name}: peer_id={user_id}")
    except Exception as e:
        print(f"  {jwt_path.name}: ERROR {e}", file=sys.stderr)
        results[jwt_path.name] = {"peer_id": None, "error": str(e)}
    finally:
        try:
            client.stop()
        except Exception:
            pass

# Save results
out = pathlib.Path("/root/.config/baleobala/jwt-peer-ids.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(results, indent=2))
print(f"\nSaved to {out}")
print(json.dumps(results, indent=2))
PYEOF

echo ""
echo "==> Peer ID probe complete. Check /root/.config/baleobala/jwt-peer-ids.json"
echo ""
echo "Next steps (run on server):"
echo "  1. Set COORDINATOR_PEER_ID to the peer_id of account-1.jwt"
echo "  2. Run: bash /opt/baleobala/scripts/install-systemd-coordinator.sh"
echo "  3. Run: bash /opt/baleobala/scripts/install-systemd-exit-mesh.sh"
