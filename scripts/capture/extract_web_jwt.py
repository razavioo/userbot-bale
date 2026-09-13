import urllib.request
import json
import websocket
import sys
import os

def main():
    print("Looking for active web.bale.ai Chrome tab...")
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=2)
    except Exception as e:
        print(f"Error connecting to Chrome DevTools: {e}")
        print("Make sure Chrome is running with --remote-debugging-port=9222")
        sys.exit(1)
        
    tabs = json.loads(resp.read())
    ws_url = next((t.get("webSocketDebuggerUrl") for t in tabs if "web.bale.ai/chat" in t.get("url", "")), None)
    
    if not ws_url:
        print("ERROR: Could not find an active web.bale.ai/chat tab.")
        sys.exit(1)
        
    print(f"Connecting to CDP WebSocket: {ws_url}")
    ws = websocket.create_connection(ws_url, suppress_origin=True)
    
    script = """
    new Promise((resolve) => {
        let req = indexedDB.open("kv");
        req.onsuccess = (e) => {
            let db = e.target.result;
            // The Bale web client stores auth under "store"
            try {
                let tx = db.transaction("store", "readonly");
                let st = tx.objectStore("store");
                let items = {};
                st.openCursor().onsuccess = (ce) => {
                    let cursor = ce.target.result;
                    if(cursor) {
                        items[cursor.key] = cursor.value;
                        cursor.continue();
                    } else {
                        resolve(JSON.stringify(items));
                    }
                };
            } catch(e) { resolve(JSON.stringify({})); }
        };
        req.onerror = () => resolve(JSON.stringify({error: "db open failed"}));
    })
    """
    
    cmd = {"id": 1, "method": "Runtime.evaluate", "params": {"expression": script, "awaitPromise": True}}
    ws.send(json.dumps(cmd))
    res = json.loads(ws.recv())
    ws.close()
    
    try:
        kv_store = json.loads(res.get("result", {}).get("result", {}).get("value", "{}"))
    except Exception:
        kv_store = {}
        
    auth_data = kv_store.get('persist:auth', '{}')
    if isinstance(auth_data, dict):
        pass # Already parsed
    elif isinstance(auth_data, str):
        try:
            auth_data = json.loads(auth_data)
        except Exception:
            pass
            
    access_token = None
    if isinstance(auth_data, dict) and 'token' in auth_data:
        try:
            token_obj = json.loads(auth_data['token'])
            access_token = token_obj.get('access_token')
        except Exception:
            access_token = auth_data.get('token')
            
    # Try looking in 'auth' directly
    if not access_token and 'auth' in kv_store:
        try:
            auth_obj = json.loads(kv_store['auth']) if isinstance(kv_store['auth'], str) else kv_store['auth']
            access_token = auth_obj.get('token', {}).get('access_token', None)
        except Exception:
            pass
            
    # Try document.cookie fallback
    if not access_token:
        ws = websocket.create_connection(ws_url, suppress_origin=True)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {"expression": "document.cookie"}}))
        cookie_res = json.loads(ws.recv())
        ws.close()
        cookies = cookie_res.get("result", {}).get("result", {}).get("value", "")
        for c in cookies.split(";"):
            if "access_token=" in c:
                access_token = c.split("access_token=")[1].strip()
                break

    if access_token:
        print("\nSUCCESS! Extracted JWT.")
        out_file = "/tmp/bale_jwt.txt"
        with open(out_file, "w") as f:
            f.write(access_token)
        print(f"Saved to: {out_file}")
        print("You can now run: userbot-bale bale-call --peer-id <ID>")
    else:
        print("\nCould not find 'access_token' in IndexedDB or Cookies. Try logging in again.")
        print("IndexedDB kv dump:", kv_store.keys())

if __name__ == '__main__':
    main()
