import pytest

from app.replays import map_feature_schema as ms
from test_map_feature_tagger import run_node, run_page, page_data
from test_quiet_routes import rope_case


@pytest.mark.parametrize('bad', [None, {'world_z': ms.unresolved(), 'tolerance': ms.known(.2, 'm')},
    {'world_z': ms.known(-3, 'm'), 'tolerance': ms.known(.6, 'm')},
    {'world_z': ms.known(-3, 'm'), 'tolerance': ms.known(.2, 'm')}])
def test_landing_validation_and_runtime_projection_match_python(bad):
    _, mf = rope_case()
    mf['routes'][0]['endpoints'][0]['landing'] = bad
    got = run_node('function run(p) {return {report:F.validate(p, [], []), digest:F.runtimeDigest(p)};}', mf)
    report = ms.validate(mf)
    assert [(e['where'], e['code']) for e in got['report']['errors']] == [(e['where'], e['code']) for e in report.errors]
    assert got['digest'] == ms.runtime_digest(mf)


def test_quiet_direction_edit_preserves_physical_directions_and_persists():
    got = run_page('''function run(p) {
      const page = openPage(p.data, {map:'Ascent'}), A = page.api;
      A.createPreset('vertical_rope');
      const route = A.fe.Ascent.mf.routes.slice(-1)[0], before = JSON.stringify(route.directions);
      function all(el) {return [el,...(el.children || []).flatMap(all)];}
      all(page.el('featProps')).find(el => el.textContent === 'Use current directions as quiet estimates').click();
      const afterCopy = JSON.stringify(A.fe.Ascent.mf.routes.slice(-1)[0].quiet_directions);
      all(page.el('featProps')).find(el => el.textContent === 'Remove quiet direction').click();
      const result = A.fe.Ascent.mf.routes.slice(-1)[0];
      return {before, afterCopy, physical:JSON.stringify(result.directions),
              quiet:result.quiet_directions, restored:page.reopen().api.fe.Ascent.mf.routes.slice(-1)[0]};
    }''', {'data': page_data(future=False)})
    assert got['before'] == got['afterCopy'] == got['physical']
    assert len(got['quiet']) == 1 and got['restored']['quiet_directions'] == got['quiet']
