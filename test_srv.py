import threading
import time
import urllib.request
import sys
sys.path.insert(0, "c:\\projects\\aegis")
import server

def run_srv():
    server.run_server(8082)

t = threading.Thread(target=run_srv, daemon=True)
t.start()
time.sleep(2)
try:
    req = urllib.request.Request("http://localhost:8082/api/projects/c0450f3a-539c-4591-8aa6-08a040e92c2e", method="DELETE")
    print("Sending DELETE")
    urllib.request.urlopen(req)
except Exception as e:
    print(e.read().decode())
