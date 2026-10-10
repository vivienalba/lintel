import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from .models import now


class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("QUALITY_DATA_DIR", "data")).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.artifacts = self.root/"artifacts"
        self.artifacts.mkdir(exist_ok=True)
        self.path = self.root/"quality.sqlite3"
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS scans (id TEXT PRIMARY KEY,url TEXT,created TEXT,payload TEXT);
                CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY,payload TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY,url TEXT,hours INTEGER,next_run TEXT,enabled INTEGER,options TEXT);
                CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY,created TEXT,message TEXT,seen INTEGER DEFAULT 0);
            ''')

    def connect(self):
        return sqlite3.connect(self.path,timeout=20)

    def save_scan(self, scan):
        data = scan.to_dict() if hasattr(scan,"to_dict") else scan
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO scans VALUES (?,?,?,?)",(data["id"],data["url"],data["created"],json.dumps(data)))
        return data

    def scans(self):
        with self.connect() as db:
            return [{"id":r[0],"url":r[1],"created":r[2]} for r in db.execute("SELECT id,url,created FROM scans ORDER BY created DESC,rowid DESC LIMIT 100")]

    def scan(self,id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM scans WHERE id=?",(id,)).fetchone()
        return json.loads(row[0]) if row else None

    def reviews(self):
        with self.connect() as db:
            return {r[0]:json.loads(r[1]) for r in db.execute("SELECT id,payload FROM reviews")}

    def review(self,id,payload):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO reviews VALUES (?,?)",(id,json.dumps(payload)))

    def setting(self,key,default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self,key,value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",(key,json.dumps(value)))

    def schedule(self,url,hours,options):
        due = (datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat(timespec="seconds")
        with self.connect() as db:
            db.execute("INSERT INTO schedules(url,hours,next_run,enabled,options) VALUES (?,?,?,?,?)",(url,int(hours),due,1,json.dumps(options)))

    def schedules(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM schedules ORDER BY id")]

    def toggle_schedule(self,id,enabled):
        with self.connect() as db:
            db.execute("UPDATE schedules SET enabled=? WHERE id=?",(int(enabled),id))

    def claim_due(self):
        # Transactional claim prevents two workers from running the same job.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT 1",(now(),)).fetchone()
            if not row:
                return None
            due = (datetime.now(timezone.utc)+timedelta(hours=row["hours"])).isoformat(timespec="seconds")
            db.execute("UPDATE schedules SET next_run=? WHERE id=?",(due,row["id"]))
            return dict(row)

    def notify(self,message):
        with self.connect() as db:
            db.execute("INSERT INTO notifications(created,message) VALUES (?,?)",(now(),message))

    def notifications(self):
        with self.connect() as db:
            return [{"id":r[0],"created":r[1],"message":r[2],"seen":r[3]} for r in db.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 50")]

    def mark_read(self):
        with self.connect() as db:
            db.execute("UPDATE notifications SET seen=1")


def compare_scans(before,after):
    old = {i["id"]:i for i in before["issues"]}
    new = {i["id"]:i for i in after["issues"]}
    # Disappearance is not proof of a fix if scope or options changed.
    comparable = before["options"] == after["options"] and after.get("complete",False) and {p["url"] for p in before["pages"]}.issubset({p["url"] for p in after["pages"]})
    return {"new":[new[k] for k in new.keys()-old.keys()],"persistent":[new[k] for k in new.keys()&old.keys()],"not_seen":[old[k] for k in old.keys()-new.keys()],"comparable":comparable}
