"""Offline fixtures exercise the real scanner without making HTTP requests."""
from .models import Options
from .network import Response
from .scanner import run_scan

class DemoClient:
    def __init__(self):
        self.count=0
    def fetch(self,url,**kwargs):
        self.count+=1
        pages={
            "https://sample-studio.test/":'''<!doctype html><html lang="en"><head><title>Studio Sample</title><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="An offline example for exploring website reviews."></head><body><h1>A considered approach to design.</h1><p>This is sample data, not a live website scan.</p><a href="/work">Selected work</a><a href="/old-project">Previous project</a><a href="#booking">Book a call</a><img src="/missing.jpg"><form><input name="email" type="email" required><button>Send inquiry</button></form></body></html>''',
            "https://sample-studio.test/work":'''<!doctype html><html lang="en"><head><title>Studio Sample</title></head><body><h1>Selected work</h1><p>Coming soon</p><a href="/">Home</a><a href="mailto:hello">Email</a></body></html>'''
        }
        if url in pages:
            return Response(url,200,{"content-type":"text/html; charset=utf-8"},pages[url].encode(),elapsed=.12)
        return Response(url,404,{"content-type":"text/plain"},b"Not found",elapsed=.04)

def create_demo():
    scan=run_scan("https://sample-studio.test/",Options(max_pages=3),client=DemoClient())
    scan.notes.insert(0,"SAMPLE DATA: generated from built-in fictional HTML fixtures. No real website was scanned.")
    return scan
