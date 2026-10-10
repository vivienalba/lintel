"""Run scheduled scans. Keep this process running on an always-on machine."""
import argparse
import json
import time
from sitecheck.storage import Store,compare_scans
from sitecheck.models import Options,now
from sitecheck.scanner import run_scan


def tick(store):
    store.set_setting("worker_heartbeat",now())
    job=store.claim_due()
    if not job:
        return False
    try:
        previous=next((s for s in store.scans() if s["url"]==job["url"]),None)
        scan=store.save_scan(run_scan(job["url"],Options(**json.loads(job["options"])),artifact_dir=store.artifacts))
        new=len(compare_scans(store.scan(previous["id"]),scan)["new"]) if previous else len(scan["issues"])
        store.notify(f"Scheduled scan completed for {scan['url']}: {new} newly observed finding(s). Scan {scan['id'][:8]}.")
    except Exception as exc:
        store.notify(f"Scheduled scan could not finish for {job['url']}: {exc}")
    return True


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--once",action="store_true",help="Process one due job and exit.")
    args=parser.parse_args()
    store=Store()
    while True:
        worked=tick(store)
        if args.once:
            break
        if not worked:
            time.sleep(20)
