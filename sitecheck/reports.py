import csv
import io
import json
from html import escape
from pathlib import Path
from PIL import Image, ImageChops, ImageEnhance


def safe_cell(value):
    value=str(value or "")
    return "'"+value if value.lstrip().startswith(("=","+","-","@","\t","\r")) else value


def csv_report(scan,reviews):
    output=io.StringIO()
    fields=["page","category","severity","title","evidence","fix","target","status","owner","due","blocker","notes"]
    writer=csv.DictWriter(output,fieldnames=fields)
    writer.writeheader()
    for issue in scan["issues"]:
        row={**issue,**reviews.get(issue["id"],{})}
        writer.writerow({k:safe_cell(row.get(k,"")) for k in fields})
    return output.getvalue().encode("utf-8-sig")


def issue_markdown(issue,review=None):
    review=review or {}
    return "\n".join(["# "+issue["title"],"", "Page: "+issue["page"],"Result: "+issue["severity"],"", "## Evidence",issue["evidence"],"", "## Suggested action",issue["fix"],"", "Target: "+issue["target"],"Owner: "+review.get("owner",""),"Due: "+review.get("due",""),"",review.get("notes","")])


def pdf_report(scan,reviews,brand="Website Quality Checker",logo=None,ai_reviews=None):
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image as PDFImage,KeepTogether
    from reportlab.lib.pagesizes import A4
    buffer=io.BytesIO()
    doc=SimpleDocTemplate(buffer,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=45,bottomMargin=45)
    styles=getSampleStyleSheet()
    fonts=Path(reportlab.__file__).parent/"fonts"
    brand_fonts=Path(__file__).resolve().parents[1]/'web'/'assets'
    if "ReviewSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ReviewSans",str(brand_fonts/'SpaceMono-Regular.ttf')))
        pdfmetrics.registerFont(TTFont("ReviewBold",str(brand_fonts/'OpenSauceSans-SemiBold.ttf')))
        pdfmetrics.registerFont(TTFont("ReviewHeading",str(brand_fonts/'OpenSauceSans-Bold.ttf')))
    for style in styles.byName.values():
        style.fontName="ReviewSans"
    for name in ("Title","Heading1","Heading2","Heading3"):
        styles[name].fontName="ReviewHeading"
    styles['Heading3'].fontSize=13
    styles['Heading3'].leading=18
    styles.add(ParagraphStyle(name="BodySmall",fontName="ReviewSans",fontSize=8,leading=13,spaceAfter=7,splitLongWords=True,textColor=colors.black))
    styles["Title"].textColor=colors.HexColor("#303596")
    styles['Title'].alignment=TA_LEFT
    styles['Title'].fontSize=28
    styles['Title'].leading=33
    def p(value,style="BodySmall"):
        return Paragraph(escape(str(value)).replace("\n","<br/>"),styles[style])
    story=[]
    if logo and Path(logo).exists():
        im=Image.open(logo)
        w,h=im.size
        story.append(PDFImage(str(logo),width=100,height=100*h/w if h/w<.8 else 80))
    story += [p(brand,"Title"),p("Website review / client handover","Heading2"),p(scan["url"]),p("Scanned: "+scan["created"]),Spacer(1,12)]
    counts=[sum(i["severity"]==s for i in scan["issues"]) for s in ("Needs fixing","Review","Couldn't verify")]
    table=Table([["PAGES INSPECTED","NEEDS FIXING","REVIEW","UNVERIFIED"],[len(scan["pages"]),*counts]],colWidths=[127]*4,rowHeights=[35,54])
    table.setStyle(TableStyle([("FONTNAME",(0,0),(-1,0),"ReviewSans"),("FONTNAME",(0,1),(-1,-1),"ReviewHeading"),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#303596")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("TEXTCOLOR",(0,1),(-1,-1),colors.black),("LINEBELOW",(0,1),(-1,1),.5,colors.HexColor('#303596')),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEADING",(0,1),(-1,-1),29),("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),("FONTSIZE",(0,0),(-1,0),7),("FONTSIZE",(0,1),(-1,-1),24)]))
    story += [table,Spacer(1,18),p("Scope and limitations","Heading2")]
    story.append(p("Automated checks and guided manual review. This is not a certification of accessibility, security, legal compliance, or search performance."))
    story.append(p("Scan scope complete: "+str(scan.get("complete",False))))
    story.append(p("No numerical quality score is assigned. Counts represent observed findings, not certification."))
    for note in scan["notes"]:
        story.append(p(note))
    story.append(p("Findings and handover notes","Heading2"))
    for issue in sorted(scan["issues"],key=lambda i:{"Needs fixing":0,"Review":1,"Couldn't verify":2}.get(i["severity"],3)):
        review=reviews.get(issue["id"],{})
        story.append(KeepTogether([Spacer(1,10),p(issue["title"],"Heading3"),p(issue["severity"]+" | "+review.get("status","Open")+" | "+issue["category"])]))
        for label,value in [("Page",issue["page"]),("Evidence",issue["evidence"]),("Suggested action",issue["fix"]),("Target",issue["target"]),("Owner",review.get("owner","")),("Due",review.get("due","")),("Launch blocker",str(review.get("blocker",False))),("Notes",review.get("notes",""))]:
            if value:
                story.append(p(label+": "+value))
    if scan.get("journey"):
        story.append(p("Journey reachability","Heading2"))
        for step in scan["journey"]:
            story.append(p(step["url"]+" | "+step["result"]+" | "+step["detail"]))
    if ai_reviews:
        story.append(Spacer(1, 14))
        story.append(p("AI-generated recommendations (advisory)", "Heading2"))
        story.append(p("These are interpretations of saved scan evidence, not verified findings. No new AI request was made to create this report."))
        summary = ai_reviews.get("summary")
        if summary and summary.get("text"):
            story.append(p("AI scan summary", "Heading3"))
            story.append(p(summary["text"][:12000]))
        for result in ai_reviews.get("issue_reviews", [])[:12]:
            if result.get("text"):
                story.append(p("AI explanation for finding " + str(result.get("issue_id", "")), "Heading3"))
                story.append(p(result["text"][:5000]))
        for result in ai_reviews.get("deep_reviews", [])[:10]:
            story.append(p("AI page review: " + str(result.get("page", "")), "Heading3"))
            if result.get("summary"):
                story.append(p(result["summary"][:3500]))
            for item in result.get("findings", [])[:6]:
                story.append(p(str(item.get("title", "Suggested review")), "Heading3"))
                for field in ("reason", "suggested_fix", "verification"):
                    if item.get(field):
                        story.append(p(field.replace("_", " ").title() + ": " + str(item[field])[:1600]))
    def footer(canvas,doc):
        canvas.setFont("ReviewSans",8)
        canvas.setFillColor(colors.HexColor("#303596"))
        canvas.drawString(42,25,"Website review - findings apply to the recorded scan scope.")
        canvas.drawRightString(A4[0]-42,25,str(doc.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return buffer.getvalue()


def visual_diff(first,second):
    a,b=Image.open(first).convert("RGB"),Image.open(second).convert("RGB")
    size=(max(a.width,b.width),max(a.height,b.height))
    canvases=[]
    for im in (a,b):
        canvas=Image.new("RGB",size,"white");canvas.paste(im,(0,0));canvases.append(canvas)
    diff=ImageChops.difference(*canvases)
    mask=diff.convert("L").point(lambda x:255 if x>20 else 0)
    changed=sum(mask.histogram()[1:])/(size[0]*size[1])
    tinted=Image.blend(canvases[1],Image.new("RGB",size,(120,127,246)),.65)
    result=Image.composite(tinted,canvases[1],mask)
    output=io.BytesIO();result.save(output,format="PNG")
    return output.getvalue(),changed
