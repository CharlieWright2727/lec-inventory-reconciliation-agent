for port in 8001 8002 8003; do
  curl -s "http://localhost:$port/inventory" |
    python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["system"]["id"], len(d["items"]))'
done