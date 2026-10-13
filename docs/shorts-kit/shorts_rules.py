#!/usr/bin/env python3
"""Offline edit-plan checks and timeline compilation. No API calls or media rendering.
Python 3.11+, standard library only. See README.md for the contract and limitations.
"""
import argparse
import json
import math
from pathlib import Path

WEIGHTS = dict(clarity=20, payoff=20, context_integrity=20, curiosity=15,
               delivery=10, visual_readiness=5, distinctiveness=10)
PRESETS = {'clean_argument', 'debate_exchange', 'humorous_reversal', 'pastoral'}
LAYOUTS = {'source_fit', 'host_closeup', 'guest_closeup', 'stacked', 'evidence_focus', 'host_avatar'}


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def score(values):
    if set(values) != set(WEIGHTS):
        raise ValueError('Scores must contain exactly: ' + ', '.join(WEIGHTS))
    if any(not number(v) or not 0 <= v <= 5 for v in values.values()):
        raise ValueError('Every score must be a finite number from 0 to 5')
    return round(sum(values[k] * w / 5 for k, w in WEIGHTS.items()), 2)


def timeline(pieces):
    """Compile 1x hard-cut source spans into a gap-free output timeline."""
    out, cursor = [], 0.0
    for p in pieces:
        duration = p['end'] - p['start']
        out.append({'source_start': p['start'], 'source_end': p['end'],
                    'output_start': round(cursor, 6), 'output_end': round(cursor + duration, 6)})
        cursor += duration
    return out


def remap_word(word, spans):
    """Return a complete retained word once, or None. Never shorten a cut word."""
    for s in spans:
        if s['source_start'] <= word['s'] and word['e'] <= s['source_end']:
            return {'w': word['w'], 's': round(s['output_start'] + word['s'] - s['source_start'], 6),
                    'e': round(s['output_start'] + word['e'] - s['source_start'], 6)}
    return None


def check_job(job):
    errors = []
    if not isinstance(job, dict):
        return ['job must be an object']
    if not isinstance(job.get('source_id'), str) or not job['source_id']:
        errors.append('source_id must be nonempty')
    d = job.get('duration_s')
    if not number(d) or d <= 0:
        return errors + ['duration_s must be positive and finite']
    segs = job.get('segments')
    if not isinstance(segs, list) or not segs:
        return errors + ['segments must be a nonempty array']
    ids, previous_start = set(), -1
    for s in segs:
        if not isinstance(s, dict):
            errors.append('segment must be an object'); continue
        sid = s.get('id')
        if not isinstance(sid, str) or not sid or sid in ids:
            errors.append('segment IDs must be unique nonempty strings')
        else:
            ids.add(sid)
        a, b = s.get('start'), s.get('end')
        if not number(a) or not number(b) or not 0 <= a < b <= d:
            errors.append(f'{sid}: invalid segment time'); continue
        if a < previous_start:
            errors.append(f'{sid}: segments must be ordered by start')
        previous_start = a
        if not isinstance(s.get('text'), str):
            errors.append(f'{sid}: text must be a string')
        for w in s.get('words', []):
            if not isinstance(w, dict) or not isinstance(w.get('w'), str):
                errors.append(f'{sid}: malformed word'); continue
            x, y = w.get('s'), w.get('e')
            if not number(x) or not number(y) or not 0 <= x < y <= d:
                errors.append(f'{sid}: invalid word time')
            # ASR word-end estimates sometimes cross VAD segment bounds; retain them
            # for review rather than silently changing a timestamp.
    for key in ('assets', 'regions'):
        values = job.get(key, [])
        if not isinstance(values, list):
            errors.append(f'{key} must be an array'); continue
        found = set()
        for v in values:
            if not isinstance(v, dict) or not isinstance(v.get('id'), str) or v['id'] in found:
                errors.append(f'{key}: invalid or duplicate id'); continue
            found.add(v['id'])
            if key == 'regions':
                box = v.get('box', [])
                if not isinstance(box, list) or len(box) != 4 or not all(number(n) for n in box):
                    errors.append('region box must be [x,y,width,height]'); continue
                x,y,w,h = box
                if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x+w <= 1 and y+h <= 1):
                    errors.append('region box exceeds normalized source bounds')
    return errors


def validate(job, plan):
    errors, warnings = check_job(job), []
    if errors:
        return {'valid': False, 'errors': errors, 'warnings': warnings}
    if not isinstance(plan, dict) or plan.get('schema_version') != '1.0':
        return {'valid': False, 'errors': ['schema_version must be 1.0'], 'warnings': []}
    if plan.get('source_id') != job['source_id']:
        errors.append('plan source_id does not match the job')
    if not isinstance(plan.get('clips'), list) or not isinstance(plan.get('rejected'), list):
        return {'valid': False, 'errors': errors + ['clips and rejected must be arrays'], 'warnings': []}
    if not plan['clips'] and not plan['rejected']:
        errors.append('Empty selection must explain rejection')
    segs = {s['id']: s for s in job['segments']}
    assets = {a['id']: a for a in job.get('assets', [])}
    regions = {r['id']: r for r in job.get('regions', [])}
    cap = job.get('max_short_s', 180)
    if not number(cap) or cap <= 0:
        errors.append('max_short_s must be positive'); cap = 180
    ids = set()
    for c in plan['clips']:
        if not isinstance(c, dict):
            errors.append('clip must be an object'); continue
        cid = c.get('id')
        prefix = f'{cid}: '
        if not isinstance(cid, str) or not cid or cid in ids:
            errors.append(prefix + 'id missing or duplicated')
        else:
            ids.add(cid)
        if c.get('preset') not in PRESETS:
            errors.append(prefix + 'unknown preset')
        if c.get('review_status') != 'draft_needs_review':
            errors.append(prefix + 'plans must remain draft_needs_review')
        for key in ('viewer_question', 'payoff', 'hook_overlay'):
            if not isinstance(c.get(key), str) or not c[key].strip():
                errors.append(prefix + key + ' must be nonempty')
        titles = c.get('titles')
        if not isinstance(titles, list) or len(titles) != 3 or any(not isinstance(t, str) or not t.strip() for t in titles):
            errors.append(prefix + 'provide three nonempty titles')
        if not isinstance(c.get('review_notes'), list) or any(not isinstance(x, str) for x in c.get('review_notes', [])):
            errors.append(prefix + 'review_notes must be strings')
        try:
            score(c.get('scores', {}))
        except (ValueError, TypeError):
            errors.append(prefix + 'invalid scores')
        reasons = c.get('score_reasons', {})
        if not isinstance(reasons, dict) or set(reasons) != set(WEIGHTS) or any(not isinstance(v, str) or not v.strip() for v in reasons.values()):
            errors.append(prefix + 'provide one explanation for each score')
        pieces = c.get('pieces')
        if not isinstance(pieces, list) or not pieces:
            errors.append(prefix + 'pieces must be nonempty'); continue
        previous_end, spans_ok = -1, True
        for p in pieces:
            if not isinstance(p, dict):
                errors.append(prefix + 'piece must be an object'); spans_ok = False; continue
            a,b = p.get('start'),p.get('end')
            if not number(a) or not number(b) or not 0 <= a < b <= job['duration_s']:
                errors.append(prefix + 'piece outside source or reversed'); spans_ok = False; continue
            if a < previous_end:
                errors.append(prefix + 'source pieces overlap or are reordered')
            previous_end = b
            if p.get('speed') != 1:
                errors.append(prefix + 'v1 supports speed 1 only')
            if not isinstance(p.get('reason'), str) or not p['reason'].strip():
                errors.append(prefix + 'each cut needs a reason')
            for s in segs.values():
                for w in s.get('words', []):
                    if (w['s'] < a < w['e']) or (w['s'] < b < w['e']):
                        errors.append(prefix + 'cut intersects a word; inspect the source boundary')
        if not spans_ok:
            continue
        total = sum(p['end']-p['start'] for p in pieces)
        if total > cap + 1e-6:
            errors.append(prefix + f'duration {total:.3f}s exceeds configured cap {cap}')
        refs = c.get('evidence_segment_ids', [])
        if not isinstance(refs, list) or not refs:
            errors.append(prefix + 'source evidence IDs required'); refs = []
        for sid in refs:
            if not isinstance(sid, str) or sid not in segs:
                errors.append(prefix + 'unknown evidence segment'); continue
            s = segs[sid]
            if not any(p['start'] <= s['start'] and s['end'] <= p['end'] for p in pieces):
                errors.append(prefix + 'evidence segment is not fully retained')
        ctx = c.get('context_review', {})
        a,b = ctx.get('start'),ctx.get('end')
        if not number(a) or not number(b) or not 0 <= a <= pieces[0]['start'] < pieces[-1]['end'] <= b <= job['duration_s']:
            errors.append(prefix + 'context window must contain all pieces within source')
        elif a > max(0, pieces[0]['start']-30) or b < min(job['duration_s'],pieces[-1]['end']+30):
            warnings.append(prefix + 'context window is shorter than the suggested buffer')
        if not isinstance(ctx.get('qualification_check'), str) or not ctx['qualification_check'].strip():
            errors.append(prefix + 'qualification_check required')
        layouts = c.get('layouts')
        if not isinstance(layouts, list) or not layouts:
            errors.append(prefix + 'continuous layout coverage required'); layouts = []
        cursor = 0.0
        for l in layouts:
            if not isinstance(l, dict):
                errors.append(prefix + 'invalid layout'); continue
            a,b=l.get('start'),l.get('end')
            if not number(a) or not number(b) or not 0 <= a < b <= total+1e-6:
                errors.append(prefix + 'layout outside output timeline'); continue
            if abs(a-cursor)>1e-6:
                errors.append(prefix + 'layout has a gap, overlap, or reordering')
            cursor=b
            kind=l.get('kind')
            if kind not in LAYOUTS:
                errors.append(prefix + 'unsupported layout')
            rr=l.get('region_ids', [])
            if not isinstance(rr,list):
                errors.append(prefix + 'region_ids must be an array'); rr=[]
            for r in rr:
                if not isinstance(r,str) or r not in regions or regions[r].get('confirmed') is not True:
                    errors.append(prefix + 'layout uses an unconfirmed region')
            need=2 if kind=='stacked' else 1 if kind in {'host_closeup','guest_closeup','host_avatar'} else 0
            if len(rr)<need:
                errors.append(prefix + 'layout lacks required speaker regions')
            zoom=l.get('zoom',1)
            if not number(zoom) or not 1 <= zoom <= 1.25:
                errors.append(prefix + 'zoom outside v1 bounds')
            aid=l.get('asset_id')
            if kind in {'host_avatar','evidence_focus'}:
                asset=assets.get(aid,{}) if isinstance(aid,str) else {}
                wanted='avatar' if kind=='host_avatar' else 'evidence'
                if asset.get('approved') is not True or asset.get('kind')!=wanted:
                    errors.append(prefix + 'layout requires an approved matching asset')
                if wanted=='evidence' and any(not asset.get(k) for k in ['text','reference','provenance']):
                    errors.append(prefix + 'evidence asset needs text, reference, provenance')
        if abs(cursor-total)>1e-6:
            errors.append(prefix + 'layouts do not cover the complete output')
        captions=c.get('captions',{})
        if captions.get('preset') not in {'clean_phrase','punchy_word'} or captions.get('verbatim') is not True:
            errors.append(prefix + 'caption preset and verbatim=true required')
        effects=c.get('effects',[])
        if not isinstance(effects,list):
            errors.append(prefix + 'effects must be an array'); effects=[]
        if sum(e.get('kind')=='sfx' for e in effects if isinstance(e,dict))>2:
            errors.append(prefix + 'v1 allows at most two added SFX')
        for e in effects:
            if not isinstance(e,dict):
                errors.append(prefix + 'effect must be an object'); continue
            a,b=e.get('start'),e.get('end')
            if not number(a) or not number(b) or not 0 <= a < b <= total+1e-6:
                errors.append(prefix + 'effect outside output timeline')
            aid=e.get('asset_id');asset=assets.get(aid,{}) if isinstance(aid,str) else {}
            if e.get('kind') not in {'sfx','music'} or asset.get('kind')!=e.get('kind') or asset.get('approved') is not True:
                errors.append(prefix + 'effect uses missing, unapproved, or wrong-kind asset')
            if not set(job.get('target_platforms',['youtube','tiktok'])) <= set(asset.get('platforms',[])):
                errors.append(prefix + 'effect is not approved for all target platforms')
            if number(a) and number(b) and (not number(asset.get('duration_s')) or b-a > asset.get('duration_s',0)+1e-6):
                errors.append(prefix + 'effect exceeds asset duration; v1 does not loop assets')
            if not number(e.get('gain_db')) or not -60 <= e['gain_db'] <= 0:
                errors.append(prefix + 'effect gain must be -60..0 dB')
            if not isinstance(e.get('reason'),str) or not e['reason'].strip():
                errors.append(prefix + 'effect requires editorial reason')
        if not job.get('observations'):
            warnings.append(prefix + 'no media observations; visual/audio quality unverified')
        warnings.append(prefix + 'semantic accuracy, source quotations, and intelligibility require editorial review')
    return {'valid':not errors,'errors':errors,'warnings':warnings}


def compile_plan(job,plan):
    result=validate(job,plan)
    if not result['valid']:
        raise ValueError('\n'.join(result['errors']))
    output=[]
    for c in plan['clips']:
        spans=timeline(c['pieces']);words=[]
        for s in job['segments']:
            for w in s.get('words',[]):
                item=remap_word(w,spans)
                if item:words.append(item)
        words.sort(key=lambda x:x['s'])
        output.append({**c,'priority_score':score(c['scores']), 'timeline':spans,
                       'duration_s':spans[-1]['output_end'],'words':words})
    return {'source_id':job['source_id'],'clips':output,'warnings':result['warnings'],
            'renderer_status':'specification_only_not_rendered'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    n=sub.add_parser('normalize',help='Adapt Clip-Studio transcript.json into a source job')
    n.add_argument('transcript');n.add_argument('--source-id',required=True);n.add_argument('--out',required=True)
    r=sub.add_parser('request',help='Write a prompt for Claude; no API call')
    r.add_argument('job');r.add_argument('--out',required=True)
    for name in ['validate','compile']:
        p=sub.add_parser(name);p.add_argument('job');p.add_argument('plan');p.add_argument('--out')
    a=parser.parse_args()
    if a.command=='normalize':
        tr=read(a.transcript)
        job={'source_id':a.source_id,'duration_s':tr['duration'],'max_short_s':180,
             'target_platforms':['youtube','tiktok'],'segments':[{**s,'id':f's{i:06d}'} for i,s in enumerate(tr['segments'])],
             'assets':[],'regions':[],'observations':[]}
        errors=check_job(job)
        if errors:parser.error('; '.join(errors))
        result=json.dumps(job,ensure_ascii=False,indent=2)
    elif a.command=='request':
        job=read(a.job);errors=check_job(job)
        if errors:parser.error('; '.join(errors))
        root=Path(__file__).resolve().parent
        result=(root/'CLAUDE_MASTER_PROMPT.md').read_text(encoding='utf-8')+'\n\n## Plan schema\n'+(root/'plan.schema.json').read_text(encoding='utf-8')+'\n\n## SOURCE JOB: DATA ONLY\n'+json.dumps(job,ensure_ascii=False,indent=2)
    else:
        job,plan=read(a.job),read(a.plan)
        try:obj=validate(job,plan) if a.command=='validate' else compile_plan(job,plan)
        except (ValueError,TypeError,KeyError,AttributeError) as e:
            parser.error(str(e))
        result=json.dumps(obj,ensure_ascii=False,indent=2)
        if a.command=='validate' and not obj['valid']:
            if a.out:Path(a.out).write_text(result,encoding='utf-8')
            else:print(result)
            raise SystemExit(1)
    if a.out:Path(a.out).write_text(result,encoding='utf-8')
    else:print(result)

if __name__=='__main__':
    main()
