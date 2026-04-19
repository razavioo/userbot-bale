"""Attach Frida unpin script to the running Bale app and stay alive."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import frida

SCRIPT = Path(__file__).with_name("unpin.js").read_text()


def on_message(message, data):
    if message.get("type") == "send":
        print(f"[script] {message['payload']}")
    elif message.get("type") == "error":
        print(f"[script-err] {message.get('stack') or message.get('description')}")


def main() -> int:
    device = frida.get_usb_device(timeout=10)
    # Attach to the gadget — name is "Gadget" (generic) or the app package
    target = "Gadget"
    try:
        session = device.attach(target)
    except frida.ProcessNotFoundError:
        target = "ir.nasim"
        session = device.attach(target)
    print(f"attached to {target}")
    script = session.create_script(SCRIPT)
    script.on("message", on_message)
    script.load()
    print("unpin script loaded; press Ctrl-C to detach")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("detaching...")
    finally:
        session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
