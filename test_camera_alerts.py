import json
import urllib.request

try:
    response = urllib.request.urlopen('http://localhost:8000/alerts/latest?limit=100').read()
    data = json.loads(response)
    
    print(f"Response type: {type(data)}")
    
    # Handle both list and dict responses
    if isinstance(data, list):
        alerts = data
    elif isinstance(data, dict):
        alerts = data.get("alerts", [])
    else:
        alerts = []
    
    total = len(alerts)
    print(f"Total alerts: {total}")
    
    cam_alerts = [a for a in alerts if a.get("source") == "camera_watchdog"]
    print(f"Camera watchdog alerts: {len(cam_alerts)}")
    
    if cam_alerts:
        print("\nRecent camera alerts:")
        for ca in cam_alerts[:5]:
            print(f"  {ca['subType']}: {ca['message']} [{ca['severity']}]")
    else:
        print("\nNo camera watchdog alerts yet. Recent alerts sample:")
        for i, a in enumerate(alerts[:3]):
            print(f"  [{i}] {a.get('source')} - {a.get('subType')}")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
