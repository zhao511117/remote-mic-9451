const fs=require('fs'),vm=require('vm'),assert=require('assert');
const file=process.argv[2];
const context={module:{exports:{}}};
vm.runInNewContext(fs.readFileSync(file,'utf8').replace('__CONFIG__','{}'),context);
const decide=context.module.exports.decide;
let checks=0;
function expect(selected,bytes,active,previous,suppress,trigger,next) {
  const d=decide(selected,bytes,active,previous);
  assert.equal(d.suppress,suppress);assert.equal(d.trigger,trigger);assert.equal(d.previous,next);checks++;
}
for(const key of [6,7,10,11,12,13]) {
  expect(true,[key,0],true,0,true,key,key);
  expect(true,[key,0],true,key,true,0,key);
  expect(false,[key,0],true,0,false,0,0);
  expect(true,[key,0],false,0,false,0,key);
  expect(true,[0,0],true,key,false,0,0);
}
for(const key of [0,1,2,3,4,5,8,9]) expect(true,[key,0],true,0,false,0,key);
for(const bytes of [[6],[6,0,0],[6,1],[14,0],[255,0]]) expect(true,bytes,true,0,false,0,0);
console.log(`${checks} policy replay checks passed`);
