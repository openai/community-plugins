import test from 'node:test';
import assert from 'node:assert/strict';
import { renderHtml, renderSvg } from '../../../plugins/pcba-engineering/lib/render.mjs';

const pkg = {
  board:{id:'sensor',name:'Sensor board',revision:'A',widthMm:30,heightMm:20},variant:'production',quantity:10,
  bom:[{ref:'R1',value:'1K',package:'0603',category:'smt',populated:true},{ref:'C1',value:'100n',package:'0603',category:'smt',populated:true},{ref:'FID1',category:'fiducial',populated:false}],
  placements:[{ref:'C1',xMm:12,yMm:8,rotationDeg:0,side:'top'}],
  authoritativePlacements:[{ref:'R1',xMm:6,yMm:8,rotationDeg:0,side:'top'},{ref:'C1',xMm:12,yMm:8,rotationDeg:0,side:'top'}],
  visual:{outline:[[0,0],[30,0],[30,20],[0,20]],pads:[{ref:'R1',xMm:5.4,yMm:8,widthMm:.7,heightMm:1,shape:'rect',side:'top'}],tracks:[{x1:6,y1:8,x2:12,y2:8,width:.2,side:'top'}],holes:[{xMm:2,yMm:2,diameterMm:2}]}
};
const report = {status:'fail',counts:{errors:1},findings:[{id:'missing',code:'missing_placement',severity:'error',refs:['R1'],message:'R1 is required but absent from the assembly export.'}],scope:{materialReadiness:'unknown'}};

test('missing export retains source reference and distinguishes the two views',()=>{
  const source=structuredClone(pkg);const html=renderHtml(pkg,report,{revision:'R001'});
  assert.match(html,/Source design/);assert.match(html,/Assembly export/);
  assert.match(html,/data-ref="R1"/);assert.match(html,/missing rows, not observed physical defects/);
  assert.match(html,/R001/);assert.match(html,/Material readiness/);assert.match(html,/not assessed/);
  assert.deepEqual(pkg,source);
});

test('untrusted fields cannot add HTML, SVG attributes or script endings',()=>{
  const evil=structuredClone(pkg);evil.board.name='<img src=x onerror=alert(1)>';evil.bom[0].ref='R1"><script>alert(1)</script>';evil.bom[0].value='</script><img onerror=alert(1)>';
  evil.authoritativePlacements[0].ref=evil.bom[0].ref;
  const bad=structuredClone(report);bad.findings[0].message='<iframe src=https://evil.example>';bad.findings[0].refs=[evil.bom[0].ref];
  const html=renderHtml(evil,bad);assert.equal(html.includes('<img'),false);assert.equal(html.includes('<iframe'),false);
  assert.equal(html.includes('<script>alert'),false);assert.match(html,/\\u003c\/script>/);
  assert.match(html,/Content-Security-Policy/);assert.match(html,/script-src &#39;sha256-/);assert.match(html,/connect-src &#39;none&#39;/);
});

test('geometry uses canonical millimetres, y axis and no inferred copper',()=>{
  const svg=renderSvg(pkg,report);assert.match(svg,/x1="6" y1="12" x2="12" y2="12"/);
  assert.match(svg,/translate\(6,12\)/);assert.match(svg,/30 × 20 mm/);
  const noGeometry=renderSvg({...pkg,visual:{}},report);assert.equal(noGeometry.includes('<line class="copper'),false);
  assert.equal(svg.includes('NaN'),false);assert.equal(svg.includes('undefined'),false);
});

test('DNP and fiducial rows are not diagnosed as missing placements by rendering',()=>{
  const html=renderHtml(pkg,report);assert.match(html,/data-ref="FID1" data-issue="false"/);
  assert.match(html,/Do not populate/);
});

test('pass view does not turn unknown material or electrical facts into passes',()=>{
  const html=renderHtml({...pkg,placements:pkg.authoritativePlacements},{...report,status:'pass',findings:[]});
  assert.match(html,/Package consistency verified/);assert.match(html,/Material readiness<\/div><strong class="unknown">unknown/);
  assert.match(html,/Electrical qualification<\/div><strong>not assessed/);
  assert.equal(html.includes('https://'),false);
});

test('case-insensitive references preserve labels without inventing missing placements',()=>{
  const p=structuredClone(pkg);p.placements=p.authoritativePlacements.map(row=>({...row,ref:row.ref.toLowerCase()}));
  p.authoritativePlacements=p.authoritativePlacements.map(row=>({...row,ref:row.ref.toLowerCase()}));
  p.bom.push({ref:'J1',category:'tht',populated:true,value:'Header'});
  const html=renderHtml(p,{status:'pass',findings:[],scope:{}});
  assert.match(html,/data-ref="R1" data-issue="false"/);
  assert.match(html,/data-ref="J1" data-issue="false"/);
  assert.equal(html.includes('class="reference side-top has-issue missing"'),false);
  assert.match(html,/aria-label="R1"/);
});

test('source cutouts remain voids and clip copper geometry',()=>{
  const svg=renderSvg({...pkg,visual:{...pkg.visual,cutouts:[[[8,8],[10,8],[10,10],[8,10]]]}},report);
  assert.match(svg,/clip-rule="evenodd"/);assert.match(svg,/fill-rule="evenodd"/);
  assert.match(svg,/M 8,12 L 10,12 L 10,10 L 8,10 Z/);
  assert.match(svg,/clip-path="url\(#board-material\)"/);
});
