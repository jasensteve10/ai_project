"""Reproducible, read-only EDA of the source election PDF and ingested CSV.

Run through the pipeline: python -m src.pipeline.eda
Writes figures/, tables/, summary.json and manifest.json; interpretation lives in the report.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import tempfile
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
# Only matplotlib's cache: overriding XDG_CACHE_HOME would also hide the Hugging Face model cache.
os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'edan-matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import numpy as np
import pandas as pd
import pdfplumber
from src.preprocessing.ingestion import (
    PDF_PATH, OUTPUT_DIR, COLUMNS, INTEGER_COLUMNS, PERCENT_COLUMNS,
    CONSTITUENCY_COLUMNS, extract_pdf, transform_data, validate_data,
)
from src.preprocessing.extract import _clean_val, _is_header_row

BLUE, ORANGE, TEAL, GRAY = '#24588b', '#c46620', '#218278', '#7a8794'
SOURCE = 'Source: supplied EDAN 2025 PDF and ingested CSV; analysis of observed records.'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def md_table(df, digits=2):
    def fmt(v):
        if isinstance(v, (float, np.floating)):
            return '' if pd.isna(v) else f'{v:,.{digits}f}'
        return str(v).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(map(str, df.columns)) + ' |',
                      '| ' + ' | '.join(['---'] * len(df.columns)) + ' |'] +
                     ['| ' + ' | '.join(fmt(v) for v in row) + ' |' for row in df.itertuples(index=False, name=None)])


def profile_pdf(pdf_path, fresh):
    pages, candidate_cells, printed = [], [], {}
    with pdfplumber.open(pdf_path) as pdf:
        metadata = {k: str(v) for k, v in (pdf.metadata or {}).items()}
        for n, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            rows = [row for t in tables for row in t]
            candidates = [row for row in rows if len(row) == 16 and not _is_header_row(row) and (_clean_val(row[11]) or _clean_val(row[12]))]
            candidate_cells.extend(candidates)
            for row in rows:
                if len(row) == 16 and _clean_val(row[0]) == 'TOTAL':
                    printed = {COLUMNS[i]: int(_clean_val(row[i]).replace(' ', '')) for i in [3, 4, 5, 7, 8, 9, 13]}
            pages.append({'page': n, 'width_pt': page.width, 'height_pt': page.height,
                          'text_characters': len(page.chars), 'embedded_images': len(page.images),
                          'rectangles': len(page.rects), 'large_background_rectangles': sum(min(o['width'], o['height']) >= 2 for o in page.rects),
                          'default_tables': len(tables), 'default_rows_including_headers': len(rows),
                          'default_candidate_rows': len(candidates),
                          'corrected_candidate_rows': int((fresh.source_page == n).sum())})
    raw = pd.DataFrame(candidate_cells, columns=COLUMNS)
    profile = pd.DataFrame([{'column': col, 'raw_candidate_slots': len(raw),
                             'raw_empty_slots': int(raw[col].fillna('').map(_clean_val).eq('').sum()),
                             'interpretation': ('Merged constituency cells; emptiness is structural' if col in CONSTITUENCY_COLUMNS else
                                                'Blank means not marked elected' if col == 'elu' else 'Candidate-level value')}
                            for col in COLUMNS])
    return pd.DataFrame(pages), profile, printed, metadata


def derive_units(df):
    c = df[CONSTITUENCY_COLUMNS].drop_duplicates('circonscription_id').set_index('circonscription_id')
    g = df.groupby('circonscription_id')
    c['source_pages'] = g.source_page.agg(lambda x: ', '.join(map(str, sorted(x.unique()))))
    c['entries'] = g.size()
    c['party_labels'] = g.parti.nunique()
    c['candidate_votes'] = g.score.sum()
    c['elected_rows'] = g.elu.sum()
    c['turnout'] = c.votants / c.inscrits
    c['invalid_rate'] = c.bulletins_nuls / c.votants
    c['blank_rate'] = c.bulletins_blancs_nb / c.suffrages_exprimes
    c['registered_per_polling_station'] = c.inscrits / c.nb_bureaux_vote
    c['winner_votes'] = g.apply(lambda x: x.loc[x.elu, 'score'].sum(), include_groups=False)
    c['winner_share_expressed'] = c.winner_votes / c.suffrages_exprimes
    c['winner_share_candidate_votes'] = c.winner_votes / c.candidate_votes
    c['runner_up_votes'] = g.score.agg(lambda x: x.nlargest(2).iloc[1] if len(x) > 1 else np.nan)
    c['margin_votes'] = c.winner_votes - c.runner_up_votes
    c['margin_pp_expressed'] = 100 * c.margin_votes / c.suffrages_exprimes
    c['effective_entries'] = g.score.agg(lambda x: 1 / ((x / x.sum()) ** 2).sum())
    c['winner_is_max'] = c.winner_votes.eq(g.score.max())
    c['top_score_ties'] = g.score.agg(lambda x: int((x == x.max()).sum()))
    r = c.groupby('region').agg(constituencies=('entries', 'size'), entries=('entries', 'sum'),
        registered=('inscrits', 'sum'), voters=('votants', 'sum'), invalid=('bulletins_nuls', 'sum'),
        expressed=('suffrages_exprimes', 'sum'), blanks=('bulletins_blancs_nb', 'sum'),
        candidate_votes=('candidate_votes', 'sum'), mean_constituency_turnout=('turnout', 'mean'))
    r['weighted_turnout'] = r.voters / r.registered
    r['invalid_rate'] = r.invalid / r.voters
    r['blank_rate'] = r.blanks / r.expressed
    r['registered_share'] = r.registered / r.registered.sum()
    r['weighted_minus_mean_pp'] = 100 * (r.weighted_turnout - r.mean_constituency_turnout)
    r = r.sort_values('weighted_turnout', ascending=False)
    p = df.groupby('parti').agg(entries=('parti', 'size'), constituencies=('circonscription_id', 'nunique'),
                              votes=('score', 'sum'), elected_rows=('elu', 'sum'))
    p['entry_share'] = p.entries / len(df)
    p['vote_share_nonblank'] = p.votes / df.score.sum()
    p['vote_share_expressed'] = p.votes / c.suffrages_exprimes.sum()
    p['elected_row_share'] = p.elected_rows / df.elu.sum()
    p['elected_rows_per_entry'] = p.elected_rows / p.entries
    return c, r, p.sort_values('votes', ascending=False)


def describe_frame(frame, columns, unit):
    rows = []
    for col in columns:
        x = frame[col].dropna()
        rows.append({'unit': unit, 'variable': col, 'n': len(x), 'missing_or_not_applicable': int(frame[col].isna().sum()),
                     'mean': x.mean(), 'std_ddof1': x.std(), 'min': x.min(), 'p05': x.quantile(.05),
                     'q25': x.quantile(.25), 'median': x.median(), 'q75': x.quantile(.75),
                     'p95': x.quantile(.95), 'max': x.max(), 'skewness': x.skew()})
    return pd.DataFrame(rows)


def dictionary():
    """Curated column definitions shared with the retrieval corpus."""
    return pd.read_csv(ROOT / 'src/semantic/data_dictionary.csv')


def save_figure(fig, name, out):
    fig.savefig(out / 'figures' / f'{name}.png', dpi=220, bbox_inches='tight', facecolor='white')
    fig.savefig(out / 'figures' / f'{name}.svg', bbox_inches='tight', facecolor='white')
    plt.close(fig)


def figures(df, c, r, p, pages, raw_profile, out):
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'axes.titlesize': 12,
                         'axes.spines.top': False, 'axes.spines.right': False, 'svg.fonttype': 'none',
                         'axes.labelcolor': '#233042', 'text.color': '#233042', 'axes.axisbelow': True})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), layout='constrained')
    axes[0].bar(pages.page, pages.corrected_candidate_rows, color=BLUE, label='Corrected extraction')
    axes[0].plot(pages.page, pages.default_candidate_rows, '.', color=ORANGE, label='Default extraction')
    axes[0].set(xlabel='PDF page', ylabel='Candidate/list rows', title='A. Coverage of all 35 source pages')
    axes[0].legend(fontsize=8, loc='lower left')
    rp = raw_profile.iloc[:11]
    axes[1].barh(rp.column, 100*rp.raw_empty_slots/rp.raw_candidate_slots, color=GRAY)
    axes[1].invert_yaxis(); axes[1].set(xlabel='Empty raw table slots (%)', xlim=(0,100), title='B. Merged cells create structural blanks')
    fig.suptitle('Figure 1. Source PDF structure and extraction coverage', fontsize=14)
    save_figure(fig,'01_source_structure',out)

    fig, axes = plt.subplots(1, 2, figsize=(11,4.2),layout='constrained')
    estimates=[100*c.votants.sum()/c.inscrits.sum(),100*c.turnout.mean(),100*df.votants.sum()/df.inscrits.sum()]
    axes[0].barh(['National turnout\n(voter-weighted)','Mean constituency turnout\n(equal weight)','Incorrect ratio from\ncandidate rows'],estimates,color=[BLUE,TEAL,ORANGE])
    axes[0].invert_yaxis(); axes[0].set(xlim=(0,53), xlabel='Turnout (%)',title='A. Aggregation changes the estimand')
    for y,val in enumerate(estimates): axes[0].text(val+.7,y,f'{val:.2f}%',va='center')
    counts=c.entries.value_counts().sort_index()
    axes[1].bar(counts.index,counts.values,color=BLUE)
    axes[1].set(xlabel='Candidate/list entries per constituency',ylabel='Constituencies',title='B. Repetition is unequal across constituencies')
    axes[1].xaxis.set_major_locator(MaxNLocator(integer=True))
    fig.suptitle('Figure 2. Why the unit of analysis matters (205 constituencies)',fontsize=14)
    save_figure(fig,'02_units_and_aggregation',out)

    fig, axes=plt.subplots(2,2,figsize=(11,7.4),layout='constrained')
    axes[0,0].hist(c.turnout*100,bins=np.arange(0,105,5),color=BLUE,edgecolor='white')
    axes[0,0].axvline(100*c.votants.sum()/c.inscrits.sum(),color=ORANGE,ls='--',label='National turnout')
    axes[0,0].set(xlabel='Turnout (%)',ylabel='Constituencies',title='A. Turnout'); axes[0,0].legend(fontsize=8)
    axes[0,1].hist(c.inscrits,bins=np.geomspace(c.inscrits.min(),c.inscrits.max(),17),color=BLUE,edgecolor='white')
    axes[0,1].set(xscale='log',xlabel='Registered voters (log scale)',ylabel='Constituencies',title='B. Constituency size')
    axes[1,0].hist(c.invalid_rate*100,bins=np.arange(0,31,1),color=TEAL,edgecolor='white')
    axes[1,0].set(xlabel='Invalid / voters (%)',ylabel='Constituencies',title='C. Invalid-ballot rate; all observations retained')
    axes[1,0].annotate('141: Adzopé\n28.11%',xy=(28.1,1),xytext=(16,30),arrowprops={'arrowstyle':'->'},fontsize=9)
    axes[1,1].hist(c.blank_rate*100,bins=16,color=TEAL,edgecolor='white')
    axes[1,1].set(xlabel='Blank / expressed (%)',ylabel='Constituencies',title='D. Blank-ballot rate')
    fig.suptitle('Figure 3. Constituency-level distributions (n = 205)',fontsize=14)
    save_figure(fig,'03_constituency_distributions',out)

    fig,ax=plt.subplots(figsize=(10.5,10.5),layout='constrained')
    y=np.arange(len(r)); ax.barh(y,100*r.weighted_turnout,color=BLUE,height=.68,label='Voter-weighted turnout')
    ax.scatter(100*r.mean_constituency_turnout,y,facecolors='white',edgecolors=ORANGE,s=30,zorder=3,label='Mean constituency turnout')
    ax.set_yticks(y,[f'{name} (n={int(row.constituencies)})' for name,row in r.iterrows()]); ax.invert_yaxis()
    ax.axvline(100*c.votants.sum()/c.inscrits.sum(),color=GRAY,ls='--',label='National voter-weighted turnout')
    ax.set(xlim=(0,105),xlabel='Turnout (%)',title='Figure 4. Turnout by source region/district (33 labels)')
    ax.legend(loc='lower right',fontsize=8)
    save_figure(fig,'04_regional_turnout',out)

    top=p.head(10); remainder=p.iloc[10:].sum(numeric_only=True)
    plot=pd.concat([top,pd.DataFrame([remainder],index=['Other 33 source labels'])])
    fig,axes=plt.subplots(1,3,figsize=(12,6),sharey=True,layout='constrained')
    for ax,col,title,color in zip(axes,['entry_share','vote_share_nonblank','elected_row_share'],['Entries (n=1,125)','Nonblank votes (n=2,913,991)','Elected rows (n=205)'],[GRAY,BLUE,TEAL]):
        ax.barh(plot.index,100*plot[col],color=color); ax.set(xlim=(0,85),xlabel='Share (%)',title=title)
        for y,v in enumerate(plot[col]): ax.text(100*v+.7,y,f'{100*v:.1f}',va='center',fontsize=8)
    axes[0].invert_yaxis()
    fig.suptitle('Figure 5. Party/grouping labels: entries, votes and elected rows\nINDEPENDANT pools independent entries; elected rows are not seats',fontsize=13)
    save_figure(fig,'05_party_comparison',out)

    contested=c[c.entries>1]
    fig,axes=plt.subplots(1,2,figsize=(11,4.6),layout='constrained')
    axes[0].hist(100*c.winner_share_expressed,bins=np.arange(0,105,5),color=BLUE,edgecolor='white')
    axes[0].axvline(50,color=GRAY,ls='--');axes[0].set(xlabel='Winner votes / expressed (%)',ylabel='Constituencies',title='A. Winner share (all 205 constituencies)')
    axes[1].hist(contested.margin_pp_expressed,bins=np.arange(0,105,5),color=TEAL,edgecolor='white')
    axes[1].set(xlabel='Winner minus runner-up (percentage points)',ylabel='Constituencies',title=f'B. Winning margins (n={len(contested)} with 2+ entries)')
    fig.suptitle('Figure 7. Electoral competition within observed constituencies',fontsize=14)
    save_figure(fig,'07_competitiveness',out)

    fig,axes=plt.subplots(1,2,figsize=(11,4.8),layout='constrained')
    colors=np.where(c.entries.eq(1),ORANGE,BLUE)
    axes[0].scatter(c.inscrits,100*c.turnout,c=colors,alpha=.7,s=28,edgecolors='white',linewidths=.3)
    axes[0].set(xscale='log',xlabel='Registered voters (log scale)',ylabel='Turnout (%)',title='A. Size and turnout')
    axes[1].scatter(c.entries,100*c.turnout,c=colors,alpha=.6,s=28,edgecolors='white',linewidths=.3)
    axes[1].set(xlabel='Number of candidate/list entries',ylabel='Turnout (%)',title='B. Entries and turnout (no causal interpretation)')
    axes[1].xaxis.set_major_locator(MaxNLocator(integer=True))
    from matplotlib.lines import Line2D
    axes[0].legend(handles=[Line2D([0],[0],marker='o',linestyle='',color=BLUE,label='2+ entries'),Line2D([0],[0],marker='o',linestyle='',color=ORANGE,label='1 entry')],fontsize=8)
    fig.suptitle('Figure 8. Constituency-level associations (n = 205)',fontsize=14)
    save_figure(fig,'08_associations',out)

    cols=['inscrits','turnout','entries','invalid_rate','blank_rate','winner_share_expressed']
    labels=['Registered','Turnout','Entries','Invalid rate','Blank rate','Winner share']
    corr=c[cols].rank().corr()
    fig,ax=plt.subplots(figsize=(8,6.5),layout='constrained')
    im=ax.imshow(corr,vmin=-1,vmax=1,cmap='RdBu_r')
    ax.set_xticks(range(len(cols)),labels,rotation=35,ha='right');ax.set_yticks(range(len(cols)),labels)
    for i in range(len(cols)):
        for j in range(len(cols)):
            val=corr.iloc[i,j];ax.text(j,i,f'{val:.2f}',ha='center',va='center',color='white' if abs(val)>.6 else '#202020')
    fig.colorbar(im,ax=ax,label='Spearman rank correlation')
    ax.set_title('Figure 9. Descriptive rank correlations (205 constituencies)')
    save_figure(fig,'09_correlations',out)

    fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    axes[0].hist(100*df.score_pct,bins=np.arange(0,105,5),color=BLUE,edgecolor='white')
    axes[0].set(xlabel='Candidate/list share of expressed ballots (%)',ylabel='Candidate/list rows',title='A. Score share (n=1,125)')
    freq=p.entries.value_counts().sort_index()
    axes[1].bar(range(len(freq)),freq.values,color=TEAL)
    axes[1].set_xticks(range(len(freq)),[str(i) for i in freq.index],rotation=45)
    axes[1].set(xlabel='Entries per source party/grouping label',ylabel='Number of labels',title='B. Uneven label support (43 labels)')
    fig.suptitle('Figure 6. Candidate-level distributions and label imbalance',fontsize=14)
    save_figure(fig,'06_candidate_distributions',out)


def run_analysis(pdf_path=PDF_PATH, csv_path=OUTPUT_DIR/'edan_2025_resultats.csv', out=ROOT/'outputs/eda'):
    pdf_path, csv_path, out = Path(pdf_path), Path(csv_path), Path(out)
    for folder in [out, out/'tables', out/'figures']:
        folder.mkdir(parents=True,exist_ok=True)
    source_hashes={'pdf':sha(pdf_path),'csv':sha(csv_path)}
    df=pd.read_csv(csv_path,dtype={'circonscription_id':'string'},keep_default_na=False)
    raw=extract_pdf(pdf_path)
    fresh=transform_data(raw)
    validate_data(df)
    keys=['source_page','source_table','source_row']
    left=df.sort_values(keys).reset_index(drop=True)
    right=fresh.sort_values(keys).reset_index(drop=True)
    pd.testing.assert_frame_equal(left,right,check_dtype=False,check_exact=False,rtol=1e-12,atol=1e-12)
    pages,raw_profile,printed,pdf_metadata=profile_pdf(pdf_path,fresh)
    c,r,p=derive_units(df)
    tables={}
    def save(name,frame,index=False):
        frame.to_csv(out/'tables'/f'{name}.csv',index=index)
        tables[name]=frame
    save('pdf_page_profile',pages)
    save('raw_column_profile',raw_profile)
    save('data_dictionary',dictionary())
    profile=pd.DataFrame([{'column':col,'csv_dtype':str(df[col].dtype),'missing_cells':int(df[col].isna().sum()),
                           'empty_strings':int(df[col].astype(str).str.strip().eq('').sum()),
                           'distinct_values':int(df[col].nunique()),'unique_fraction':df[col].nunique()/len(df)} for col in df])
    save('csv_column_profile',profile)
    totals={col:int(c[col].sum()) for col in INTEGER_COLUMNS if col!='score'}
    totals['score']=int(df.score.sum())
    reconciliation=pd.DataFrame([{'metric':col,'printed_pdf_total':value,'csv_total_correct_grain':totals[col],
                                  'difference':totals[col]-value} for col,value in printed.items()])
    save('national_reconciliation',reconciliation)
    checks=[]
    def check(name,failures,scope):
        checks.append({'check':name,'failures':int(failures),'scope':scope})
    check('Missing business values',df[COLUMNS].isna().sum().sum(),'1,125 rows x 16 business columns')
    check('Empty business text',df[COLUMNS[:3]+['parti','candidat']].astype(str).eq('').sum().sum(),'5 text columns')
    check('Exact duplicate business rows',df[COLUMNS].duplicated().sum(),'1,125 rows')
    check('Duplicate natural key: constituency/party/candidate',df.duplicated(['circonscription_id','parti','candidat']).sum(),'1,125 rows')
    check('Duplicate provenance tuple',df.duplicated(keys).sum(),'1,125 rows')
    check('Invalid constituency ID format',(~df.circonscription_id.str.fullmatch(r'\d{3}')).sum(),'3-digit text IDs')
    check('Conflicting constituency-level fields',df.groupby('circonscription_id')[CONSTITUENCY_COLUMNS].nunique().gt(1).sum().sum(),'205 constituencies x 11 fields')
    check('Negative counts',df[INTEGER_COLUMNS].lt(0).sum().sum(),'7 count columns')
    check('Noninteger counts',df[INTEGER_COLUMNS].mod(1).ne(0).sum().sum(),'7 count columns')
    check('Percentages outside [0,1]',((df[PERCENT_COLUMNS]<0)|(df[PERCENT_COLUMNS]>1)).sum().sum(),'3 percentage columns')
    check('Voters exceed registered',c.votants.gt(c.inscrits).sum(),'205 constituencies')
    check('Voters != invalid + expressed',c.votants.ne(c.bulletins_nuls+c.suffrages_exprimes).sum(),'205 constituencies')
    check('Candidate votes != expressed - blank',c.candidate_votes.ne(c.suffrages_exprimes-c.bulletins_blancs_nb).sum(),'205 constituencies')
    check('Elected row count differs from one',c.elected_rows.ne(1).sum(),'Observed PDF outcome; not a universal electoral rule')
    check('Marked winner is not maximum score',(~c.winner_is_max).sum(),'205 constituencies')
    check('Tied maximum score',c.top_score_ties.gt(1).sum(),'205 constituencies')
    check('National total mismatch',reconciliation.difference.ne(0).sum(),'7 totals independently read from PDF TOTAL row')
    check('Fresh extraction / ingested CSV cell differences',0,'All 21,375 cells; numeric tolerance 1e-12')
    rounding=[]
    for numerator,denominator,col in [('votants','inscrits','taux_participation'),('bulletins_blancs_nb','suffrages_exprimes','bulletins_blancs_pct'),('score','suffrages_exprimes','score_pct')]:
        error=100*(df[numerator]/df[denominator]-df[col]).abs()
        check(f'Rounding error > 0.0051 pp: {col}',error.gt(.0051).sum(),'PDF percentages printed to 0.01 percentage points')
        rounding.append({'column':col,'max_abs_difference_pp':error.max(),'tolerance_pp':.0051,'denominator':denominator})
    quality=pd.DataFrame(checks);save('quality_checks',quality);save('percentage_rounding',pd.DataFrame(rounding))
    if quality.failures.sum():
        raise ValueError('EDA integrity checks failed; inspect quality_checks.csv')
    save('constituencies',c.reset_index());save('regions',r.reset_index());save('party_labels',p.reset_index())
    save('region_party_elected_rows',pd.crosstab(df.loc[df.elu,'region'],df.loc[df.elu,'parti']).reset_index())
    stats=pd.concat([describe_frame(c,INTEGER_COLUMNS[:-1]+['turnout','invalid_rate','blank_rate','entries','party_labels','registered_per_polling_station','winner_share_expressed','margin_votes','margin_pp_expressed','effective_entries'],'constituency'),
                     describe_frame(df,['score','score_pct'],'candidate/list')],ignore_index=True)
    save('descriptive_statistics',stats)
    candidates_per_circ=c.entries.value_counts().sort_index().rename_axis('entries').reset_index(name='constituencies')
    save('entry_count_distribution',candidates_per_circ)
    text_profile=[]
    for col in ['region','circonscription_name','parti','candidat']:
        s=df[col].astype(str)
        text_profile.append({'column':col,'unique_strings':s.nunique(),'min_characters':s.str.len().min(),
            'median_characters':s.str.len().median(),'max_characters':s.str.len().max(),
            'rows_with_non_ascii':s.map(lambda x:any(ord(ch)>127 for ch in x)).sum(),
            'rows_with_replacement_character':s.str.contains('\ufffd',regex=False).sum(),
            'rows_with_repeated_spaces':s.str.contains(r'\s{2,}').sum()})
    save('text_profile',pd.DataFrame(text_profile))
    repeated=df.groupby('candidat').agg(rows=('candidat','size'),constituencies=('circonscription_id','nunique'),
                                       source_pages=('source_page',lambda x:', '.join(map(str,sorted(x.unique())))))
    save('repeated_candidate_labels',repeated[repeated.constituencies>1].sort_values('rows',ascending=False).reset_index())
    def skeleton(text):
        text=''.join(ch for ch in unicodedata.normalize('NFKD',text.upper()) if not unicodedata.combining(ch))
        return re.sub('[^A-Z0-9]','',text)
    variants=[]
    for col in ['parti','candidat','region']:
        labels=pd.DataFrame({'label':df[col].unique()})
        labels['comparison_key']=labels.label.map(skeleton)
        for key,group in labels.groupby('comparison_key'):
            if len(group)>1:
                variants.append({'column':col,'comparison_key':key,'variants':' || '.join(group.label),
                                 'handling':'Potential orthographic variants only; no automatic merge'})
    save('potential_text_variants',pd.DataFrame(variants,columns=['column','comparison_key','variants','handling']))
    flags=[];thresholds=[]
    for col in ['inscrits','turnout','invalid_rate','blank_rate','entries','registered_per_polling_station']:
        q1,q3=c[col].quantile([.25,.75]);iqr=q3-q1;lo,hi=q1-1.5*iqr,q3+1.5*iqr
        mask=(c[col]<lo)|(c[col]>hi)
        thresholds.append({'variable':col,'q25':q1,'q75':q3,'lower_fence':lo,'upper_fence':hi,'flagged_constituencies':int(mask.sum())})
        for cid,row in c.loc[mask].iterrows():
            flags.append({'circonscription_id':cid,'name':row.circonscription_name,'region':row.region,'variable':col,
                          'value':row[col],'lower_fence':lo,'upper_fence':hi,'source_pages':row.source_pages,
                          'interpretation':'Descriptive 1.5-IQR flag; retained, not evidence of error or fraud'})
    save('outlier_thresholds',pd.DataFrame(thresholds));save('outlier_flags',pd.DataFrame(flags))
    corrcols=['inscrits','turnout','entries','invalid_rate','blank_rate','winner_share_expressed','effective_entries']
    save('pearson_correlations',c[corrcols].corr().rename_axis('variable').reset_index())
    save('spearman_correlations',c[corrcols].rank().corr().rename_axis('variable').reset_index())
    sensitivity=[]
    for label,sub in [('All observed constituencies',c),('Only constituencies with 2+ entries',c[c.entries>1]),('Exclude 141 for invalid-rate sensitivity only',c.drop('141'))]:
        sensitivity.append({'subset':label,'n':len(sub),'weighted_turnout':sub.votants.sum()/sub.inscrits.sum(),
                            'mean_constituency_turnout':sub.turnout.mean(),'national_invalid_rate_for_subset':sub.bulletins_nuls.sum()/sub.votants.sum(),
                            'spearman_size_turnout':sub[['inscrits','turnout']].rank().corr().iloc[0,1],
                            'spearman_entries_turnout':sub[['entries','turnout']].rank().corr().iloc[0,1]})
    sensitivity=pd.DataFrame(sensitivity);save('sensitivity',sensitivity)
    closest=c[c.entries>1].nsmallest(10,'margin_votes')
    save('closest_contests',closest.reset_index()[['circonscription_id','circonscription_name','region','entries','winner_votes','runner_up_votes','margin_votes','margin_pp_expressed','source_pages']])
    save('single_entry_constituencies',c[c.entries==1].reset_index())
    aggregation=pd.DataFrame([
        {'calculation':'National turnout, one row per constituency','value':c.votants.sum()/c.inscrits.sum(),'interpretation':'Registered-voter-weighted rate'},
        {'calculation':'Mean exact constituency turnout','value':c.turnout.mean(),'interpretation':'Each constituency has equal weight'},
        {'calculation':'Mean printed turnout over candidate rows','value':df.taux_participation.mean(),'interpretation':'Weights constituencies by number of entries'},
        {'calculation':'Ratio of duplicated candidate-row totals','value':df.votants.sum()/df.inscrits.sum(),'interpretation':'Incorrect national aggregation'},
        {'calculation':'Inflation factor for registered totals at candidate grain','value':df.inscrits.sum()/c.inscrits.sum(),'interpretation':'Repeated constituency totals'},
    ]);save('aggregation_comparison',aggregation)
    figures(df,c,r,p,pages,raw_profile,out)
    summary={
        'source_hashes':source_hashes,'pdf_pages':len(pages),'candidate_rows':len(df),'business_columns':len(COLUMNS),
        'provenance_columns':len(keys),'constituencies':len(c),'regions':len(r),'party_labels':len(p),
        'checks':len(quality),'failed_checks':int(quality.failures.sum()),
        'national_turnout':float(c.votants.sum()/c.inscrits.sum()),'mean_exact_constituency_turnout':float(c.turnout.mean()),
        'mean_printed_constituency_turnout':float(c.taux_participation.mean()),
        'national_invalid_rate':float(c.bulletins_nuls.sum()/c.votants.sum()),'national_blank_rate_expressed':float(c.bulletins_blancs_nb.sum()/c.suffrages_exprimes.sum()),
        'single_entry_constituencies':int((c.entries==1).sum()),'contested_constituencies':int((c.entries>1).sum()),
        'winner_below_half_expressed':int((c.winner_share_expressed<.5).sum()),
        'winner_below_half_candidate_votes':int((c.winner_share_candidate_votes<.5).sum()),
        'margins_under_5pp':int(c.margin_pp_expressed.lt(5).sum()),'margins_under_1pp':int(c.margin_pp_expressed.lt(1).sum()),
        'repeated_candidate_labels_across_constituencies':int((repeated.constituencies>1).sum()),
        'outlier_flags':len(flags),'distinct_flagged_constituencies':len(set(v['circonscription_id'] for v in flags)),
        'source_pdf_metadata':pdf_metadata,
        'source_pages_visually_reviewed_this_eda':[1,21,22,30],
    }
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    manifest={'command':'python -m src.pipeline.eda','python':platform.python_version(),'platform':platform.platform(),
              'source_hashes':source_hashes,'analysis_code_sha256':sha(__file__),
              'libraries':{name:importlib.metadata.version(name) for name in ['pandas','numpy','pdfplumber','matplotlib']},
              'output_sha256':{str(path.relative_to(out)):sha(path) for path in sorted(out.rglob('*')) if path.is_file() and path.name!='manifest.json'}}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    assert source_hashes=={'pdf':sha(pdf_path),'csv':sha(csv_path)}, 'Input files changed during EDA'
    print(json.dumps({k:v for k,v in summary.items() if k not in ['source_pdf_metadata','source_hashes']},indent=2))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',type=Path,default=PDF_PATH)
    parser.add_argument('--csv',type=Path,default=OUTPUT_DIR/'edan_2025_resultats.csv')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'outputs/eda')
    args=parser.parse_args()
    run_analysis(args.pdf,args.csv,args.output_dir)


if __name__=='__main__':
    main()
