"""Reproducible, read-only EDA of the source election PDF and ingested CSV.

Run: python -m src.eda
Outputs contain descriptive statistics, not estimates of model performance.
"""
import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / 'tmp/eda/matplotlib'))
os.environ.setdefault('XDG_CACHE_HOME', str(ROOT / 'tmp/eda/cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter, FuncFormatter, MaxNLocator
import markdown
import numpy as np
import pandas as pd
import pdfplumber
from src.preprocessing.ingest import (
    PDF_PATH, OUTPUT_DIR, COLUMNS, INTEGER_COLUMNS, PERCENT_COLUMNS,
    CONSTITUENCY_COLUMNS, extract_pdf, transform_data, validate_data,
)
from src.preprocessing.pdf_helpers import _clean_val, _is_header_row

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
    meanings = {
        'region': ('constituency', 'text', 'Source region/district label; 33 observed labels.'),
        'circonscription_id': ('constituency', 'text ID', 'Three-character identifier; preserve leading zeros.'),
        'circonscription_name': ('constituency', 'text', 'Source constituency name; names can describe multiple communes.'),
        'nb_bureaux_vote': ('constituency', 'count', 'Polling stations.'),
        'inscrits': ('constituency', 'persons', 'Registered voters.'),
        'votants': ('constituency', 'persons', 'Voters; equals invalid plus expressed ballots.'),
        'taux_participation': ('constituency', 'fraction', 'Voters / registered, rounded in PDF to 0.01 percentage points.'),
        'bulletins_nuls': ('constituency', 'ballots', 'Invalid ballots; derived rate denominator is voters.'),
        'suffrages_exprimes': ('constituency', 'ballots', 'Expressed ballots in this source include blank ballots.'),
        'bulletins_blancs_nb': ('constituency', 'ballots', 'Blank ballots.'),
        'bulletins_blancs_pct': ('constituency', 'fraction', 'Blank / expressed, rounded in source.'),
        'parti': ('candidate/list', 'text', 'Party/grouping label; INDEPENDANT pools independent entries.'),
        'candidat': ('candidate/list', 'text', 'Candidate or list label; not necessarily a person or unique identity.'),
        'score': ('candidate/list', 'votes', 'Votes for the candidate/list.'),
        'score_pct': ('candidate/list', 'fraction', 'Score / expressed, rounded in source; includes blanks in denominator.'),
        'elu': ('candidate/list', 'boolean', 'True for PDF marker ELU(E); not a seat count.'),
        'source_page': ('provenance', 'integer', 'One-based PDF page of candidate row.'),
        'source_table': ('provenance', 'integer', 'One-based table after corrected extraction.'),
        'source_row': ('provenance', 'integer', 'One-based row after corrected extraction, including headers.'),
    }
    return pd.DataFrame([{'column': k, 'unit_of_observation': v[0], 'unit': v[1], 'definition': v[2]} for k,v in meanings.items()])


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


def run_analysis(pdf_path=PDF_PATH, csv_path=OUTPUT_DIR/'edan_2025_resultats.csv', out=ROOT/'docs/eda'):
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
    write_report(df,c,r,p,pages,raw_profile,quality,reconciliation,stats,sensitivity,summary,out)
    manifest={'command':'python -m src.eda','python':platform.python_version(),'platform':platform.platform(),
              'source_hashes':source_hashes,'analysis_code_sha256':sha(__file__),
              'libraries':{name:importlib.metadata.version(name) for name in ['pandas','numpy','pdfplumber','matplotlib','Markdown']},
              'output_sha256':{str(path.relative_to(out)):sha(path) for path in sorted(out.rglob('*')) if path.is_file() and path.name!='manifest.json'}}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    assert source_hashes=={'pdf':sha(pdf_path),'csv':sha(csv_path)}, 'Input files changed during EDA'
    print(json.dumps({k:v for k,v in summary.items() if k not in ['source_pdf_metadata','source_hashes']},indent=2))
    return summary


def write_report(df,c,r,p,pages,raw_profile,quality,reconciliation,stats,sensitivity,s,out):
    def pct(v): return f'{100*v:.2f}%'
    def num(v): return f'{v:,.0f}'
    contested=c[c.entries>1]
    desc=stats[stats.variable.isin(['inscrits','votants','entries','score','score_pct','turnout','invalid_rate','blank_rate','margin_pp_expressed','effective_entries'])].copy()
    desc=desc[['unit','variable','n','mean','std_ddof1','min','q25','median','q75','max']]
    rate_rows=desc.variable.isin(['turnout','invalid_rate','blank_rate','score_pct'])
    desc.loc[rate_rows,['mean','std_ddof1','min','q25','median','q75','max']]*=100
    desc.loc[rate_rows,'variable']=desc.loc[rate_rows,'variable']+' (%)'
    reg=r.reset_index()[['region','constituencies','registered','weighted_turnout','mean_constituency_turnout']].copy()
    reg[['weighted_turnout','mean_constituency_turnout']]*=100
    reg.columns=['Source region/district','Constituencies','Registered','Weighted turnout (%)','Mean constituency turnout (%)']
    party=p.head(10).reset_index()[['parti','entries','votes','vote_share_nonblank','elected_rows']].copy()
    party.vote_share_nonblank*=100
    party.columns=['Source label','Entries','Votes','Nonblank vote share (%)','Elected rows']
    ex=c.loc[['123','141','047','122'],['circonscription_name','inscrits','votants','turnout','invalid_rate','entries','source_pages']].reset_index()
    ex.turnout*=100; ex.invalid_rate*=100
    ex.columns=['ID','Constituency','Registered','Voters','Turnout (%)','Invalid (%)','Entries','PDF pages']
    sens=sensitivity.copy()
    for col in ['weighted_turnout','mean_constituency_turnout','national_invalid_rate_for_subset']:sens[col]*=100
    sens.columns=['Subset','n','Weighted turnout (%)','Mean turnout (%)','Invalid rate (%)','Spearman size/turnout','Spearman entries/turnout']
    close=contested.nsmallest(5,'margin_votes').reset_index()[['circonscription_id','circonscription_name','margin_votes','margin_pp_expressed','source_pages']]
    rankcorr=c[['inscrits','turnout','entries']].rank().corr()
    nearest=contested.nsmallest(1,'margin_votes').iloc[0]
    singleton=c[c.entries==1]
    corpus={col:int(df[col].nunique()) for col in ['region','circonscription_name','parti','candidat']}
    fragments=[]
    def section(text):fragments.append(text.strip())
    section(f'''# Exploratory data analysis: Côte d'Ivoire 2025 election results

Source PDF and ingested CSV | Analysis date: 27 September 2026

## 1. Executive findings

The supplied 35-page PDF contains **{len(df):,} candidate/list entries in {len(c)} constituencies**, grouped under **{len(r)} region/district labels and {len(p)} party/grouping labels**. The CSV has 16 business fields and three provenance fields. Its business fields contain no missing values or exact duplicate records. All {len(quality)} integrity checks pass. A fresh source extraction matches all {df.size:,} CSV cells after type conversion, and all seven printed national totals reconcile exactly.

National turnout is **{pct(s['national_turnout'])}** ({num(c.votants.sum())} voters / {num(c.inscrits.sum())} registered), whereas the equal-weight mean constituency turnout is **{pct(c.turnout.mean())}**. This difference is substantive: constituencies vary greatly in size. Directly summing repeated constituency totals over candidate rows would inflate registered voters by **{df.inscrits.sum()/c.inscrits.sum():.2f} times** and produce an incorrect **{pct(df.votants.sum()/df.inscrits.sum())}** national turnout.

RHDP-labelled entries account for **{pct(p.loc['RHDP','vote_share_nonblank'])}** of nonblank candidate/list votes and **{int(p.loc['RHDP','elected_rows'])} of {int(df.elu.sum())} elected rows**. This is not a count of parliamentary seats. INDEPENDANT is a pooled label for {int(p.loc['INDEPENDANT','entries'])} entries, not a single political party. There are **{len(singleton)} constituencies with one recorded entry** and **{len(contested)} with two or more**.

Unusual values are retained. For example, Adzopé (ID 141) records {num(c.loc['141','bulletins_nuls'])} invalid ballots among {num(c.loc['141','votants'])} voters (**{pct(c.loc['141','invalid_rate'])}**), and Odienné (ID 123) records **{pct(c.loc['123','turnout'])}** turnout. Both values were visually checked in the PDF. Their presence does not by itself establish a data error or explain the underlying electoral process.

A concise section suitable for the project report is available in [REPORT_SECTION.md](REPORT_SECTION.md). Full precision results are in the linked CSV tables; figures are available as high-resolution PNG and editable SVG.
''')
    section(f'''## 2. Sources, scope and units of observation

The analysis uses `EDAN_2025_RESULTAT_NATIONAL_DETAILS.pdf` and `edan_2025_resultats.csv`. The PDF heading identifies the election as the election of deputies to the National Assembly, with a ballot date of 27 December 2025. This EDA assesses the supplied file and its transcription; it does not independently authenticate the document against an external publisher or later revisions.

The PDF is a born-digital, landscape table document: all {len(pages)} pages contain extractable text and one default-detected table per page. Region labels run vertically; constituency labels and totals occupy merged cells; headers repeat between pages; shaded rectangles and unclosed bottom borders complicate extraction. The PDF totals row is an independent accounting target within the same source, not an external validation dataset.

Three distinct analytical units are used:

- **Candidate/list entry, n={len(df):,}:** one row per recorded candidature, identified by constituency, party/grouping and candidate/list label. The natural key is unique in this file. A row can name a person or a list.
- **Constituency, n={len(c)}:** one consistent set of registered voters, voters and ballot totals per ID, after checking every repeated field for agreement. IDs remain three-character strings from `001` to `205`, with no gaps. They are identifiers, not numeric model features or a time sequence.
- **Source region/district, n={len(r)}:** aggregates of constituencies sharing a printed label. The inventory includes autonomous districts; calling all 33 labels administrative regions would overstate the source semantics.

The same constituency totals appear on every candidate row. All national and regional voter totals are therefore computed at constituency grain. Source page/table/row locates the **candidate row** in the corrected extraction; metadata for a block spanning pages may appear on the following page.

There is one election snapshot. No demographic variables, voting-station-level observations, historical series, seat counts or list-member roster are provided. Candidate/list text cannot reliably determine gender, age, occupation or a unique person identity.
''')
    section(f'''## 3. Raw PDF exploration and ingestion fidelity

Default table extraction exposes {int(pages.default_rows_including_headers.sum()):,} table rows including headers and {int(pages.default_candidate_rows.sum()):,} candidate/list rows. Corrected extraction recovers {len(df):,} entries, including the last page-20 row (3,760 votes) omitted by the default detector. The raw column profile distinguishes structural blanks in merged cells from missing measurements; an empty election-marker cell means that the row is not marked elected.

![Source structure](figures/01_source_structure.png)

**Figure 1.** Page-level extraction counts and blank raw table slots. The raw blank percentages describe the table parser's unfilled merged-cell slots, not unavailable election statistics. The corrected CSV has no missing values in its 16 business columns.

The documented extraction repairs address background rectangles mistaken for table rules, reversed vertical text, omitted unclosed bottom rows and constituency labels split across pages. The previous audit reported 1,124 rows, 204 IDs and missing statistics for five constituencies. The corrected current CSV contains 1,125 rows and 205 IDs. This historical comparison concerns extraction quality, not changes in the election itself.

For this EDA, the source is re-extracted afresh, transformed, sorted on the three provenance fields and compared cell by cell with the existing CSV. Text, IDs and booleans must agree; numeric comparison uses tolerance 1e-12 for floating-point serialization. All {df.size:,} cells agree. This demonstrates reproducibility and consistency with the extraction rules; it is not independent manual transcription of every candidate name. Separate arithmetic reconciliation and rendered-page checks provide additional evidence.

Source pages 1, 21, 22 and 30 were visually inspected for the national totals, near-tie and high-turnout cases, the high invalid count, and one-entry blocks. The prior ingestion review also inspected pages 2, 10, 17, 20 and 31. No source observations were imputed, dropped, winsorized or relabelled for the analysis.

**National accounting reconciliation**

{md_table(reconciliation)}

The identities `voters = invalid + expressed` and `sum(candidate votes) = expressed - blank` hold separately in every constituency. In this PDF, expressed ballots include blanks: candidate shares and blank shares use expressed ballots as their denominator. A generic alternative definition of “valid votes” must not silently replace this source convention.

Detailed evidence: [page inventory](tables/pdf_page_profile.csv), [raw column profile](tables/raw_column_profile.csv), [quality checks](tables/quality_checks.csv), [rounding checks](tables/percentage_rounding.csv), and [national reconciliation](tables/national_reconciliation.csv).
''')
    section(f'''## 4. Schema, completeness and semantic checks

{md_table(dictionary())}

All 19 CSV columns are populated. There are no exact duplicate business rows, duplicate constituency/party/candidate keys or duplicate provenance tuples. Every ID maps to exactly one name and source region, and all repeated constituency fields agree. Integer counts are nonnegative, voters never exceed registrations, and stored percentage fractions lie in [0, 1]. Every constituency has one marked elected row; the marked row has the maximum score, with no tied maximum.

The source prints percentages to two decimal percentage points, so a difference up to 0.0051 percentage points is allowed when comparing them with recomputed ratios. All three source percentage columns satisfy this tolerance. Analytical ratios are recomputed from counts; the original rounded percentages remain unchanged in the input CSV.

There are {corpus['candidat']:,} distinct candidate/list strings across {len(df):,} rows. **{s['repeated_candidate_labels_across_constituencies']} exact strings occur in multiple constituencies**, often because a shared list label is reused. Names alone are not reliable record identifiers. Accents, punctuation and spacing variants remain in the source labels. A conservative comparison that removes punctuation, spacing and accents identifies potential variants in a separate table, without assuming the labels represent the same entity or merging their votes.

The source label `INDEPENDANT` pools separate entries and can appear repeatedly in one constituency. Coalitions, such as `PDCI - FPI - ADCI`, are kept separate from their component party labels. Canonical party mapping would need additional source-grounded rules.

See the [CSV column profile](tables/csv_column_profile.csv), [text profile](tables/text_profile.csv), [repeated labels](tables/repeated_candidate_labels.csv) and [potential spelling variants](tables/potential_text_variants.csv).
''')
    section(f'''## 5. National totals and aggregation effects

There are {num(c.inscrits.sum())} registered voters, {num(c.votants.sum())} voters and {num(c.nb_bureaux_vote.sum())} polling stations. National invalid-ballot rate is **{pct(s['national_invalid_rate'])}** using voters as denominator. Blank ballots are **{pct(s['national_blank_rate_expressed'])}** of expressed ballots. Candidate/list votes sum to {num(df.score.sum())} after excluding blanks.

![Aggregation effects](figures/02_units_and_aggregation.png)

**Figure 2.** National turnout weights each constituency by registered voters. Mean constituency turnout gives every constituency equal weight. The third bar deliberately shows the invalid national aggregation obtained by summing constituency totals repeated on candidate rows. The right panel explains why repetition is unequal.

The voter-weighted and equal-constituency rates differ by **{100*(c.turnout.mean()-s['national_turnout']):.2f} percentage points**. The mean of the printed turnout column over all candidate rows is a fourth quantity, **{pct(df.taux_participation.mean())}**, because it weights constituencies by the number of candidate/list entries. Questions about “average participation” therefore require an explicit definition in the SQL agent and evaluation gold answers. Full formulas and values are in [aggregation_comparison.csv](tables/aggregation_comparison.csv).
''')
    section(f'''## 6. Univariate distributions

{md_table(desc)}

Rates in this display table are percentages, and winning margins are in percentage points. Standard deviation uses ddof=1 as a conventional descriptive summary. The full [statistics table](tables/descriptive_statistics.csv) retains fractional rate units and also includes 5th/95th percentiles and skewness. Runner-up and margin variables have {len(singleton)} not-applicable values for one-entry constituencies, not missing source observations.

Constituency size is strongly uneven: registered voters range from **{num(c.inscrits.min())} to {num(c.inscrits.max())}**, with median **{num(c.inscrits.median())}**. The ten largest constituencies contain **{pct(c.inscrits.nlargest(10).sum()/c.inscrits.sum())}** of registrations. The registration histogram uses a logarithmic horizontal scale to show both typical and very large constituencies.

Turnout ranges from **{pct(c.turnout.min())} to {pct(c.turnout.max())}**, with median **{pct(c.turnout.median())}** and interquartile range **{pct(c.turnout.quantile(.25))}–{pct(c.turnout.quantile(.75))}**. Candidate/list counts range from **{int(c.entries.min())} to {int(c.entries.max())}**, with median **{c.entries.median():.0f}**.

![Constituency distributions](figures/03_constituency_distributions.png)

**Figure 3.** All 205 constituencies are included. Invalid-ballot rates use voters; blank-ballot rates use expressed ballots. The Adzopé extreme is shown on the full scale and is not removed from the histogram or national accounting.
''')
    section(f'''## 7. Regional variation

Regional turnout is calculated as `sum(voters) / sum(registered)` using unique constituencies. The highest rate is **{r.index[0]} ({pct(r.iloc[0].weighted_turnout)})** and the lowest is **{r.index[-1]} ({pct(r.iloc[-1].weighted_turnout)})**. Constituency counts and registered populations differ across these source labels, so the comparison is descriptive and is not a ranking of otherwise equivalent populations.

![Regional turnout](figures/04_regional_turnout.png)

**Figure 4.** Bars show registered-voter-weighted turnout; hollow points show mean constituency turnout. The dashed line is the national voter-weighted rate. The number of constituencies is shown next to every source label.

{md_table(reg)}

[regions.csv](tables/regions.csv) also provides invalid/blank rates, electorate shares and the difference between weighted and unweighted turnout. No geographic map is constructed because boundary geometries and validated geographic joins are absent from the supplied data.
''')
    section(f'''## 8. Party/grouping and candidate/list distributions

{md_table(party)}

The ten highest-vote source labels are shown above; all {len(p)} labels are in [party_labels.csv](tables/party_labels.csv). Nonblank vote share is `party votes / sum(all candidate votes)`. This differs from source `score_pct`, which uses expressed ballots including blanks. The table of regional elected rows is available separately, without inferring seats.

RHDP is represented in **{int(p.loc['RHDP','constituencies'])} constituencies**, PDCI-RDA in **{int(p.loc['PDCI-RDA','constituencies'])}**, and the pooled INDEPENDANT label in **{int(p.loc['INDEPENDANT','constituencies'])}**. **{int((p.entries==1).sum())} labels have only one entry**. Their observed elected-entry proportions are based on extremely small counts and should not be interpreted as general success probabilities.

![Party comparisons](figures/05_party_comparison.png)

**Figure 5.** Entries, nonblank votes and elected rows have different denominators. The same ten labels, ordered by votes, are shown in every panel; remaining labels are pooled only for visualization. Elected rows are neither seats nor the number of individual deputies. The independent label aggregates unrelated independent candidatures.

![Candidate distributions](figures/06_candidate_distributions.png)

**Figure 6.** Candidate/list score shares and source-label support are uneven. The source has {int(df.score.eq(0).sum())} zero-score entries, with candidate/list scores ranging from {num(df.score.min())} to {num(df.score.max())}. Numerical range and category frequency should inform benchmark coverage rather than encourage evaluation only on frequent labels.
''')
    section(f'''## 9. Competitiveness and close contests

There are **{len(singleton)} constituencies with a single recorded entry** ({pct(len(singleton)/len(c))}) and **{len(contested)} with two or more**. A missing runner-up in a one-entry constituency is recorded as not applicable; its margin is not set to 100 percentage points.

Among all constituencies, **{s['winner_below_half_expressed']} winners have less than 50% of expressed ballots** and **{s['winner_below_half_candidate_votes']} have less than 50% of nonblank candidate votes**. These are separate descriptive thresholds; this EDA does not infer the statutory voting rule from them. Among constituencies with two or more entries, the median winning margin is **{contested.margin_pp_expressed.median():.2f} percentage points** of expressed ballots. **{s['margins_under_5pp']}** margins are below five points and **{s['margins_under_1pp']}** below one point.

![Electoral competition](figures/07_competitiveness.png)

**Figure 7.** Winner share uses expressed ballots. Margin equals `(winner votes - second-highest votes) / expressed ballots × 100`, restricted to constituencies with at least two entries.

The five smallest raw-vote margins are:

{md_table(close)}

The closest contest is ID **{contested.margin_votes.idxmin()}**, with a **{num(nearest.margin_votes)}-vote** gap ({nearest.margin_pp_expressed:.4f} percentage points). Page 21 visibly records 1,916 and 1,914 votes for the top two entries in constituency 122. This is a useful precision test case for the SQL benchmark.

An additional descriptive measure, effective entries, is `1 / sum(p_i²)`, where `p_i` is an entry's share of nonblank candidate votes within its constituency. Its median is **{c.effective_entries.median():.2f}**, compared with a median of **{c.entries.median():.0f}** nominal entries. It measures concentration among candidature entries, not the number of political parties. Full results are in [constituencies.csv](tables/constituencies.csv).
''')
    section(f'''## 10. Associations and sensitivity

At constituency level, Spearman rank correlation between registered electorate size and turnout is **{rankcorr.loc['inscrits','turnout']:.3f}**; between entry count and turnout it is **{rankcorr.loc['entries','turnout']:.3f}**. Pearson correlations, which depend more on scale and extremes, are supplied separately. Spearman correlations are computed as Pearson correlations of average ranks, including ties.

![Constituency associations](figures/08_associations.png)

**Figure 8.** Each point is a constituency; orange identifies a single recorded entry. The size axis is logarithmic. No fitted line is presented as a causal model.

![Rank correlation matrix](figures/09_correlations.png)

**Figure 9.** Descriptive rank correlations use one row per constituency. Rates share numerators and denominators, and winner share is mechanically related to competition; correlation is not independent evidence of a causal effect.

{md_table(sens,3)}

The second row restricts analysis to constituencies with two or more recorded entries; it changes the population being described. The third omits ID 141 only to quantify sensitivity to its invalid-ballot count. The main analysis retains every observation. These checks help distinguish aggregate patterns from unusual subgroups; they do not justify replacing the reported national results.

No p-values, confidence intervals or hypothesis-test claims are used. The analysis describes all records in the supplied snapshot, not a probability sample. It cannot explain individual voting behaviour from constituency aggregates, establish causation, or generalize to other elections without additional data and assumptions.
''')
    section(f'''## 11. Unusual observations and source checks

{md_table(ex)}

A systematic 1.5×IQR rule flags values below Q1−1.5×IQR or above Q3+1.5×IQR for registrations, turnout, invalid rate, blank rate, entry count and registrations per polling station. It produces **{s['outlier_flags']} variable-level flags across {s['distinct_flagged_constituencies']} constituencies**. These flags are descriptive, depend on the selected variables and do not label electoral misconduct or automatic cleaning errors.

- **ID 141, Adzopé (page 22):** 3,423 invalid ballots, 12,179 voters, 8,756 expressed ballots. The accounting identity holds and the unusual count is visibly present in the PDF. Excluding this constituency changes the aggregate invalid rate from {pct(s['national_invalid_rate'])} to {pct(sensitivity.iloc[2].national_invalid_rate_for_subset)}; the source transcription retains it.
- **ID 123, Odienné (page 21):** 32,116 voters among 32,124 registered, one recorded entry, nine invalid ballots and zero blanks. The printed 99.98% turnout is source-faithful. IDs 170–172 on page 30 also have high turnout with single recorded entries.
- **ID 047, Yopougon:** the largest electorate, {num(c.loc['047','inscrits'])} registered voters, with turnout {pct(c.loc['047','turnout'])}. Its size materially affects voter-weighted national and regional statistics.
- **ID 122 (page 21):** the two-vote winning margin requires count-based arithmetic; rounded displayed percentages should not be used to recover exact vote differences.

Every flag includes the source page(s) in [outlier_flags.csv](tables/outlier_flags.csv); thresholds are in [outlier_thresholds.csv](tables/outlier_thresholds.csv). Most flags have arithmetic/source-extraction checks rather than a new manual visual review of every flagged cell. A separate source investigation would be needed to explain their substantive causes.
''')
    section('''## 12. Implications for the Text-to-SQL / retrieval study

1. **Encode the analytical grain.** Questions involving voters, registrations, polling stations or regional turnout must use the constituency view. Candidate totals belong in the candidate view. Include deliberate double-counting traps in benchmark questions.
2. **Define denominators in gold answers.** Distinguish weighted turnout, mean constituency turnout, expressed-ballot score shares and nonblank vote shares. Preserve full precision for computation and specify display tolerances.
3. **Evaluate source coverage and entity matching.** Include the repaired IDs 006, 042, 047, 065, 088, 115 and 135; accented names, long multi-commune labels, coalition labels and exact three-digit IDs. Repeated list names require constituency context.
4. **Cover varied query difficulty.** Include single-constituency lookups, regional aggregations, party totals, ranking, joins, close margins, one-entry cases and explicit source-page questions. Sample both common and rare entities, and report performance by stratum.
5. **Measure appropriate abstention.** Seat counts, list members, demographic explanations, causes of turnout and comparisons with other elections are unsupported by the supplied schema.
6. **Keep held-out questions independent.** Group paraphrases of the same query intent/entity combination when defining development and evaluation splits. Retrieval units should retain constituency ID and provenance. Data rows need not be treated as independent training examples; no model training is required by this project scope.

The integrity checks establish a stronger data basis for evaluation. They do not measure answer correctness, retrieval gains or model accuracy. Those require the separate controlled benchmark and experiment runner described in the research plan.
''')
    section(f'''## 13. Reproducibility and limitations

Run from the repository root:

```sh
uv pip install --python .venv/bin/python -r requirements-eda.txt
.venv/bin/python -m src.eda
```

Optional arguments: `--pdf`, `--csv`, and `--output-dir`. The script reads the source PDF and CSV without altering either file, regenerates derived tables and figures, checks input hashes again at completion, and writes `summary.json` and `manifest.json`. The manifest records dependency versions, code/input hashes and output hashes. Figures use deterministic descriptive calculations with no random sampling.

- Source PDF SHA-256: `{s['source_hashes']['pdf']}`
- Ingested CSV SHA-256: `{s['source_hashes']['csv']}`

Main limitations are the single supplied election snapshot, lack of external source authentication, candidate/list ambiguity, absent seat and demographic data, exact source label variations and rounded printed percentages. Complete accounting reconciliation does not prove every string transcription or underlying electoral claim correct. The same extraction code supports fresh-extraction comparison, so arithmetic checks and rendered-source inspection are necessary complements.

The current report describes the corrected CSV only. Historical before/after audit counts are explicitly identified as extraction comparisons. No additional party consolidation, substantive electoral correction, live model request or paid API call is part of this EDA.

All numerical tables: [tables/](tables/). Figures: [figures/](figures/). Source inventory and definitions are documented above. The analysis is descriptive and preserves all observed records.
''')
    report='\n\n'.join(fragments)+'\n'
    (out/'EDA_REPORT.md').write_text(report)
    concise=f'''# Report section: data exploration and quality

The dataset consists of a 35-page PDF reporting the Côte d'Ivoire National Assembly election of 27 December 2025 and its ingested CSV. The corrected CSV contains {len(df):,} candidate/list entries across {len(c)} constituencies, {len(r)} source region/district labels and {len(p)} party/grouping labels. It includes 16 business columns and three fields identifying the source page, table and row. A candidature can represent a person or a list; elected-row counts cannot be interpreted as seat counts.

Exploration of the raw document revealed vertical region labels, merged constituency cells, shaded backgrounds, repeated headers and page-spanning blocks. These layout features explained the initial extraction defects. The corrected extractor reconstructs those structures and recovers constituency 115 and an omitted candidate/list row. A fresh extraction matches all {df.size:,} ingested CSV cells after type conversion. There are no missing business values or duplicate constituency/party/candidate keys. Constituency-level totals are consistent across repeated rows, and all {len(quality)} integrity checks pass. These results demonstrate reproducibility and arithmetic consistency, complemented by selected rendered-source checks, rather than independent manual verification of every source cell.

All seven national totals reconcile with the PDF: {num(c.inscrits.sum())} registered voters, {num(c.votants.sum())} voters, {num(c.nb_bureaux_vote.sum())} polling stations, {num(c.bulletins_nuls.sum())} invalid ballots, {num(c.suffrages_exprimes.sum())} expressed ballots, {num(c.bulletins_blancs_nb.sum())} blank ballots and {num(df.score.sum())} candidate/list votes. In every constituency, voters equal invalid plus expressed ballots, while candidate votes equal expressed minus blank ballots. Consequently, source candidate percentages use expressed ballots including blanks as their denominator. Rounded source percentages agree with recomputed ratios within 0.0051 percentage points.

The analytical grain is important. National turnout, calculated from unique constituency totals, is {pct(s['national_turnout'])}; equal-weight mean constituency turnout is {pct(c.turnout.mean())}. Registered electorate sizes range from {num(c.inscrits.min())} to {num(c.inscrits.max())}, with median {num(c.inscrits.median())}. Summing totals directly over candidate rows would multiply registrations by {df.inscrits.sum()/c.inscrits.sum():.2f} and incorrectly report turnout as {pct(df.votants.sum()/df.inscrits.sum())}. This motivates explicit metric definitions and constituency-level views in the SQL system (Figure 2).

The data also show uneven competition and label support. Constituencies contain between {int(c.entries.min())} and {int(c.entries.max())} entries; {len(singleton)} have only one recorded entry. Among the {len(contested)} constituencies with at least two entries, {s['margins_under_5pp']} winning margins are below five percentage points of expressed ballots; the closest is two votes. RHDP-labelled entries receive {pct(p.loc['RHDP','vote_share_nonblank'])} of nonblank candidate votes and account for {int(p.loc['RHDP','elected_rows'])} of {int(df.elu.sum())} elected rows. The INDEPENDANT label pools {int(p.loc['INDEPENDANT','entries'])} separate entries and is not treated as a unified party. Coalitions and source spelling variants remain distinct (Figures 5 and 7).

Unusual observations are retained and documented. Adzopé (141) records an invalid-ballot rate of {pct(c.loc['141','invalid_rate'])}, while Odienné (123) records turnout of {pct(c.loc['123','turnout'])}; both values were visually confirmed in the source. Spearman correlations of turnout with electorate size ({rankcorr.loc['inscrits','turnout']:.3f}) and entry count ({rankcorr.loc['entries','turnout']:.3f}) are descriptive constituency-level associations, without causal interpretation. Sensitivity checks separate one-entry constituencies and quantify the effect of the high invalid-ballot observation without changing the main results.

For the evaluation study, these findings motivate benchmark coverage of alternative aggregation grains, explicit percentage denominators, rare and accented entity labels, close contests, one-entry constituencies and unsupported seat/demographic questions. The dataset represents a single supplied snapshot; source reconciliation does not independently authenticate the electoral results or demonstrate model accuracy. All analysis is reproducible using `python -m src.eda`; full tables, source hashes and figure files accompany the EDA report.

Suggested figures for the main project report: **Figure 2** (unit/aggregation error) and **Figure 7** (competition). Use **Figure 4** (regional turnout) or **Figure 5** (party comparison) if space permits; retain the full EDA as supplementary material.
'''
    (out/'REPORT_SECTION.md').write_text(concise)
    body=markdown.markdown(report,extensions=['tables','fenced_code','toc'])
    for path in (out/'figures').glob('*.png'):
        body=body.replace(f'src="figures/{path.name}"',f'src="data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}"')
    html='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Election data EDA</title><style>
    body{max-width:1120px;margin:40px auto;padding:0 28px;font:16px/1.65 system-ui,sans-serif;color:#233042;background:white}
    h1{font-size:2rem;line-height:1.25;border-bottom:3px solid #24588b;padding-bottom:18px}h2{font-size:1.4rem;margin-top:2.4em;color:#24588b}p,li{max-width:96ch}
    table{border-collapse:collapse;font-size:12px;width:100%;display:block;overflow-x:auto;margin:24px 0}th{text-align:left;background:#edf2f7}th,td{padding:8px 10px;border-bottom:1px solid #dce3eb;vertical-align:top}td{min-width:50px}img{max-width:100%;height:auto;margin:16px 0}code{font-size:.87em;overflow-wrap:anywhere;background:#f1f4f7;padding:2px 4px}pre{background:#f1f4f7;padding:18px;overflow-x:auto}a{color:#24588b}strong{font-weight:650}@media print{body{max-width:none;font-size:10pt;margin:0}h2{break-after:avoid}img,table{break-inside:avoid}a{color:inherit}table{font-size:8pt}}
    </style></head><body>'''+body+'</body></html>'
    (out/'index.html').write_text(html)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',type=Path,default=PDF_PATH)
    parser.add_argument('--csv',type=Path,default=OUTPUT_DIR/'edan_2025_resultats.csv')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'docs/eda')
    args=parser.parse_args()
    run_analysis(args.pdf,args.csv,args.output_dir)


if __name__=='__main__':
    main()
