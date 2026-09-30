"""Rebuild the eight-page project report from Markdown and saved experiment traces.

No ingestion, model call, training or evaluation is performed by this command.
"""
import csv
import hashlib
import html
import importlib.metadata
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'docs/report'
SOURCE = REPORT / 'PROJECT_REPORT.md'
OUTPUT = ROOT / 'output/pdf/PROJECT_REPORT.pdf'
RETRIEVAL = ROOT / 'experiments/runs/rag-integration-retrieval-20260927/retrieval.jsonl'
LIVE = ROOT / 'experiments/runs/rag-integration-live-fixed-20260927/traces.jsonl'


def export_results():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = [json.loads(s) for s in RETRIEVAL.read_text().splitlines() if s.strip()]
    eligible = [r for r in rows if r['n_slots']]
    data = []
    for method in ['bm25', 'dense', 'hybrid']:
        cover = [r['rankers'][method]['quota_coverage']['5'] for r in eligible]
        n = len(cover)
        data.append({'method': method, 'n_questions': n,
                     'complete_contexts': sum(c['complete'] for c in cover),
                     'complete_context_rate': sum(c['complete'] for c in cover) / n,
                     'mean_slot_recall': sum(c['slot_recall'] for c in cover) / n})
    (REPORT / 'tables').mkdir(parents=True, exist_ok=True)
    with (REPORT / 'tables/retrieval_results.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(data[0])); writer.writeheader(); writer.writerows(data)
    latest = {}
    for s in LIVE.read_text().splitlines():
        r = json.loads(s)
        latest[(r['question_id'], r['condition'], r['repeat'])] = r
    with (REPORT / 'tables/live_checks.csv').open('w', newline='') as f:
        keys = ['question_id', 'condition', 'outcome', 'rows', 'api_calls', 'input_tokens', 'output_tokens']
        writer = csv.DictWriter(f, fieldnames=keys); writer.writeheader()
        writer.writerows({k: json.dumps(r[k], ensure_ascii=False) if k == 'rows' else r[k] for k in keys}
                         for r in latest.values())
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False})
    fig, ax = plt.subplots(figsize=(8.7, 2.7), layout='constrained')
    y = list(range(3)); h = 0.30
    ax.barh([i+h/2 for i in y], [100*d['complete_context_rate'] for d in data], h,
            color='#235778', label='Complete context')
    ax.barh([i-h/2 for i in y], [100*d['mean_slot_recall'] for d in data], h,
            color='#45a29b', label='Mean slot recall')
    for i, d in enumerate(data):
        ax.text(100*d['complete_context_rate']+1, i+h/2, f"{100*d['complete_context_rate']:.1f}%", va='center', fontsize=9)
        ax.text(100*d['mean_slot_recall']+1, i-h/2, f"{100*d['mean_slot_recall']:.1f}%", va='center', fontsize=9)
    ax.set_yticks(y, ['BM25', 'Multilingual E5', 'Hybrid RRF']); ax.invert_yaxis()
    ax.set_xlim(0, 113); ax.set_xticks([0,25,50,75,100]); ax.set_xlabel('Coverage (%)')
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.22), frameon=False, ncol=2)
    ax.grid(axis='x', alpha=.15); ax.set_axisbelow(True)
    (REPORT / 'figures').mkdir(exist_ok=True)
    for ext in ['png', 'svg']:
        fig.savefig(REPORT / f'figures/retrieval_coverage.{ext}', dpi=220, bbox_inches='tight')
    plt.close(fig)
    return data


def inline(text):
    # Escape first, then convert the small, controlled Markdown subset to PDF markup.
    text = html.escape(text)
    text = re.sub(r'\[([^]]+)\]\((https?://[^)]+)\)', r'<a href="\2" color="#235778">\1</a>', text)
    text = re.sub(r'`([^`]+)`', r'<font name="Courier" size="8.4">\1</font>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    return text


def export_eda_figures():
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    from matplotlib.ticker import MaxNLocator
    c = pd.read_csv(ROOT / 'docs/eda/tables/constituencies.csv')
    a = pd.read_csv(ROOT / 'docs/eda/tables/aggregation_comparison.csv')
    fig, axes = plt.subplots(1, 2, figsize=(11,4.2), layout='constrained')
    estimates = [100*c.votants.sum()/c.inscrits.sum(), 100*c.turnout.mean(),
                 100*(c.votants*c.entries).sum()/(c.inscrits*c.entries).sum()]
    axes[0].barh(['National turnout\n(voter-weighted)', 'Mean constituency turnout\n(equal weight)',
                 'Incorrect ratio from\ncandidate rows'], estimates, color=['#235778','#25857d','#bf691f'])
    axes[0].invert_yaxis(); axes[0].set(xlim=(0,53), xlabel='Turnout (%)', title='A. Aggregation changes the statistic')
    for y,v in enumerate(estimates): axes[0].text(v+.7,y,f'{v:.2f}%',va='center')
    counts = c.entries.value_counts().sort_index()
    axes[1].bar(counts.index, counts.values, color='#235778')
    axes[1].set(xlabel='Candidate/list entries per constituency', ylabel='Constituencies', title='B. Repetition varies across constituencies')
    axes[1].xaxis.set_major_locator(MaxNLocator(integer=True))
    for ext in ['png','svg']: fig.savefig(REPORT / f'figures/aggregation.{ext}',dpi=220,bbox_inches='tight')
    plt.close(fig)
    fig, axes = plt.subplots(1,2,figsize=(11,4.2),layout='constrained')
    axes[0].hist(100*c.winner_share_expressed,bins=np.arange(0,105,5),color='#235778',edgecolor='white')
    axes[0].axvline(50,color='#8c9aa3',ls='--')
    axes[0].set(xlabel='Winner votes / expressed (%)',ylabel='Constituencies',title='A. Winner share (205 constituencies)')
    axes[1].hist(c.loc[c.entries>1,'margin_pp_expressed'],bins=np.arange(0,105,5),color='#25857d',edgecolor='white')
    axes[1].set(xlabel='Winner minus runner-up (percentage points)',ylabel='Constituencies',title='B. Winning margins (194 contested constituencies)')
    for ext in ['png','svg']: fig.savefig(REPORT / f'figures/competition.{ext}',dpi=220,bbox_inches='tight')
    plt.close(fig)


def build_pdf():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
    from PIL import Image as PILImage
    font_roots = [Path('/System/Library/Fonts/Supplemental'), Path('/usr/share/fonts/truetype/liberation2')]
    fontroot = next((p for p in font_roots if (p / 'Arial.ttf').exists() or (p / 'LiberationSans-Regular.ttf').exists()), None)
    if fontroot:
        arial = (fontroot / 'Arial.ttf').exists()
        filenames = ['Arial.ttf','Arial Bold.ttf','Arial Italic.ttf','Arial Bold Italic.ttf'] if arial else [
            'LiberationSans-Regular.ttf','LiberationSans-Bold.ttf','LiberationSans-Italic.ttf','LiberationSans-BoldItalic.ttf']
        for face, fn in zip(['Body','BodyBold','BodyItalic','BodyBoldItalic'], filenames):
            pdfmetrics.registerFont(TTFont(face, str(fontroot / fn)))
        pdfmetrics.registerFontFamily('Body', normal='Body', bold='BodyBold', italic='BodyItalic', boldItalic='BodyBoldItalic')
        regular, bold = 'Body', 'BodyBold'
    else:
        regular, bold = 'Helvetica', 'Helvetica-Bold'
    ink, teal = '#17374c', '#235778'
    base = ParagraphStyle('Body', fontName=regular, fontSize=10, leading=13.3,
                          spaceAfter=8, textColor=colors.HexColor('#202d36'))
    styles = {
        'p': base,
        'h1': ParagraphStyle('Title', parent=base, fontName=bold, fontSize=26, leading=30, spaceAfter=13, textColor=colors.HexColor(ink)),
        'h2': ParagraphStyle('Section', parent=base, fontName=bold, fontSize=17, leading=21, spaceAfter=13, textColor=colors.HexColor(ink)),
        'h3': ParagraphStyle('Subsection', parent=base, fontName=bold, fontSize=11, leading=14, spaceBefore=6, spaceAfter=6, textColor=colors.HexColor(teal)),
        'caption': ParagraphStyle('Caption', parent=base, fontSize=8.5, leading=11, textColor=colors.HexColor('#52636e'), spaceAfter=10),
        'cell': ParagraphStyle('Cell', parent=base, fontSize=9, leading=11.4, spaceAfter=0),
        'bullet': ParagraphStyle('Bullet', parent=base, leftIndent=11, firstLineIndent=-8, spaceAfter=5),
    }
    for k in ['h1','h2','h3']: styles[k].keepWithNext = True
    w, h = A4
    width = w - 92
    doc = SimpleDocTemplate(str(OUTPUT), pagesize=A4, leftMargin=46, rightMargin=46,
                            topMargin=40, bottomMargin=42, title='Retrieval-Augmented Text-to-SQL for Electoral Data',
                            author='Jasen', subject='Course project: EDA, retrieval and preliminary evaluation')
    story = []
    lines = SOURCE.read_text().splitlines(); i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1; continue
        if line == '<!-- pagebreak -->':
            story.append(PageBreak()); i += 1; continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', c) for c in cells): rows.append(cells)
                i += 1
            n = len(rows[0])
            if n == 2: ratios = [.35,.65]
            elif n == 3: ratios = [.44,.28,.28]
            elif n == 4: ratios = [.43,.19,.19,.19]
            else: ratios = [1/n]*n
            if rows[0][0] == 'Observation': ratios = [.25,.48,.27]
            if rows[0][0] == 'Check': ratios = [.36,.40,.12,.12]
            cooked = [[Paragraph(inline(('**'+c+'**') if ri == 0 else c), styles['cell']) for c in row] for ri,row in enumerate(rows)]
            table = Table(cooked, colWidths=[width*r for r in ratios], hAlign='LEFT', repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e5edf2')),
                ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),
                ('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
                ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f5f8fa')]),
                ('LINEBELOW',(0,0),(-1,0),.5,colors.HexColor('#bbcbd6')),
            ]))
            story.extend([table, Spacer(1,10)]); continue
        match = re.match(r'!\[(.*?)\]\((.*?)\)', line)
        if match:
            path = (REPORT / match[2]).resolve()
            with PILImage.open(path) as im: iw, ih = im.size
            story.append(Image(str(path), width=width, height=width*ih/iw)); story.append(Spacer(1,6)); i += 1; continue
        if line.startswith('#'):
            count = len(line)-len(line.lstrip('#')); text = line[count:].strip()
            story.append(Paragraph(inline(text),styles['h'+str(count)])); i += 1; continue
        if line.startswith('- ') or re.match(r'^\d\. ',line):
            text = line[2:] if line.startswith('- ') else line
            story.append(Paragraph(('&#8226; ' if line.startswith('- ') else '') + inline(text),styles['bullet'])); i+=1; continue
        paragraph = [line]; i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r'^(#|\||!\[|<!--|- |\d\. )', lines[i].strip()):
            paragraph.append(lines[i].strip()); i+=1
        text = ' '.join(paragraph)
        style = styles['caption'] if text.startswith('*') and not text.startswith('**') else styles['p']
        story.append(Paragraph(inline(text),style))
    def footer(canvas, doc):
        canvas.saveState(); canvas.setFont(regular,8); canvas.setFillColor(colors.HexColor('#687984'))
        canvas.drawString(46,24,'Electoral RAG | Deep Learning with Python | 2026')
        canvas.drawRightString(w-46,24,str(doc.page)); canvas.restoreState()
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    doc.build(story,onFirstPage=footer,onLaterPages=footer)


def main():
    data = export_results()
    export_eda_figures()
    build_pdf()
    import pdfplumber
    with pdfplumber.open(OUTPUT) as pdf:
        pages = len(pdf.pages)
        for i,p in enumerate(pdf.pages,1):
            print(i, (p.extract_text() or '').splitlines()[0])
        if pages != 8:
            raise ValueError(f'Expected eight pages, got {pages}; inspect pagination')
    sources = [SOURCE,RETRIEVAL,LIVE,ROOT/'docs/eda/summary.json',Path(__file__),
               ROOT/'docs/eda/tables/constituencies.csv',ROOT/'docs/eda/tables/aggregation_comparison.csv']
    manifest = {'pages':pages,'output':str(OUTPUT.relative_to(ROOT)),
                'sources':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                'pdf_sha256':hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
                'versions':{p:importlib.metadata.version(p) for p in ['reportlab','matplotlib','pdfplumber']},
                'retrieval_results':data}
    (REPORT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(OUTPUT)


if __name__ == '__main__':
    main()
