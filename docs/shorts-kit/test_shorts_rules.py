import copy,json,unittest
from pathlib import Path
from shorts_rules import validate,compile_plan,score,WEIGHTS
ROOT=Path(__file__).resolve().parent
class RulesTests(unittest.TestCase):
    def setUp(self):
        self.job=json.loads((ROOT/'example_job.json').read_text())
        self.plan=json.loads((ROOT/'example_plan.json').read_text())
        self.clip=self.plan['clips'][0]
    def invalid(self,contains):
        r=validate(self.job,self.plan)
        self.assertFalse(r['valid']);self.assertTrue(any(contains in e for e in r['errors']),r)
    def test_valid(self):self.assertTrue(validate(self.job,self.plan)['valid'])
    def test_source_identity(self):
        self.plan['source_id']='other';self.invalid('source_id')
    def test_invalid_source_range(self):
        self.clip['pieces'][0]['start']=-1;self.invalid('outside source')
    def test_reordered_pieces(self):
        self.clip['pieces'].reverse();self.invalid('reordered')
    def test_word_cut(self):
        self.clip['pieces'][0]['start']=30.1;self.invalid('intersects a word')
    def test_unknown_evidence(self):
        self.clip['evidence_segment_ids']=['invented'];self.invalid('unknown evidence')
    def test_evidence_omitted(self):
        self.clip['pieces'][0]['start']=37;self.invalid('not fully retained')
    def test_gap_in_layout(self):
        self.clip['layouts'][0]['start']=1;self.invalid('gap')
    def test_unconfirmed_face(self):
        self.clip['layouts'][0].update(kind='host_closeup',region_ids=['host']);self.invalid('unconfirmed')
    def test_unapproved_sound(self):
        self.clip['effects']=[dict(kind='sfx',asset_id='boom',start=5,end=6,gain_db=-20,reason='reveal')];self.invalid('unapproved')
    def test_duration_cap(self):
        self.job['max_short_s']=10;self.invalid('exceeds configured cap')
    def test_nonfinite(self):
        self.clip['pieces'][0]['end']=float('nan');self.invalid('outside source')
    def test_speed_change(self):
        self.clip['pieces'][0]['speed']=1.2;self.invalid('speed 1')
    def test_draft_gate(self):
        self.clip['review_status']='approved';self.invalid('draft_needs_review')
    def test_score(self):self.assertEqual(score(dict.fromkeys(WEIGHTS,5)),100)
    def test_boolean_not_score(self):
        self.clip['scores']['clarity']=True;self.invalid('invalid scores')
    def test_compile_cut_remapping(self):
        c=compile_plan(self.job,self.plan)['clips'][0]
        self.assertEqual(c['duration_s'],25)
        self.assertEqual(c['timeline'][1]['output_start'],17)
        self.assertEqual(next(w for w in c['words'] if w['w']=='premise')['s'],18)
    def test_no_candidates_allowed_with_reason(self):
        self.plan['clips']=[];self.plan['rejected']=[{'source_start':0,'source_end':120,'reason':'No complete exchange'}]
        self.assertTrue(validate(self.job,self.plan)['valid'])
    def test_empty_unexplained(self):
        self.plan['clips']=[];self.invalid('explain rejection')
    def test_asset_platform_scope(self):
        self.job['assets']=[dict(id='tone',kind='sfx',approved=True,duration_s=2,platforms=['youtube'])]
        self.clip['effects']=[dict(kind='sfx',asset_id='tone',start=5,end=6,gain_db=-20,reason='reveal')]
        self.invalid('all target platforms')
    def test_no_implicit_asset_loop(self):
        self.job['assets']=[dict(id='tone',kind='sfx',approved=True,duration_s=.1,platforms=['youtube','tiktok'])]
        self.clip['effects']=[dict(kind='sfx',asset_id='tone',start=5,end=6,gain_db=-20,reason='reveal')]
        self.invalid('exceeds asset duration')
if __name__=='__main__':unittest.main()
