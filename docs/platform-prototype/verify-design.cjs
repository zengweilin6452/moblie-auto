// Dependency-free design consistency checks, NOT a PostgreSQL parser or migration test.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const sql = fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8').replace(/\r\n/g, '\n');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
const tables = JSON.parse(script.match(/const databaseTables=(\[[^\n]*\]);/)[1]);
const physical = new Map([...sql.matchAll(/CREATE TABLE (\w+) \(\n([\s\S]*?)\n\);/g)]
  .map(([, name, body]) => [name, body.split(/,\n/).map(s => s.trim())]));
assert.equal(physical.size, tables.length, 'SQL/UI table count differs');
assert.equal(new Set(tables.map(t => t.name)).size, tables.length, 'Duplicate table names');
const normalized = s => s.replace(/\s+/g, ' ').trim();
const cols = s => s.split(',').map(c => c.trim());
let foreignKeys = 0;
let columns = 0;
for (const t of tables) {
  assert.deepEqual(physical.get(t.name)?.map(normalized),
    [...t.fields.map(f => f.definition), ...t.constraints].map(normalized),
    `${t.name}: SQL differs from the UI dictionary`);
  const fields = new Map(t.fields.map(f => [f.name, f]));
  assert.equal(fields.size, t.fields.length, `${t.name}: duplicate columns`);
  columns += fields.size;
  for (const c of t.constraints) {
    const local = c.match(/^(?:UNIQUE|FOREIGN KEY) \(([^)]+)\)/);
    if (local) for (const name of cols(local[1])) assert(fields.has(name), `${t.name}.${name} missing`);
  }
  for (const entry of [...t.fields.map(f => f.definition), ...t.constraints]) {
    const ref = entry.match(/REFERENCES (\w+)\(([^)]+)\)/);
    if (!ref) continue;
    foreignKeys++;
    const target = tables.find(other => other.name === ref[1]);
    assert(target, `${t.name}: missing referenced table ${ref[1]}`);
    const targetColumns = cols(ref[2]);
    const local = entry.match(/^FOREIGN KEY \(([^)]+)\)/);
    const localColumns = local ? cols(local[1]) : [entry.split(/\s+/)[0]];
    assert.equal(localColumns.length, targetColumns.length, `${t.name}: FK arity`);
    targetColumns.forEach((name, i) => {
      const field = target.fields.find(f => f.name === name);
      assert(field, `${target.name}.${name} missing`);
      assert.equal(fields.get(localColumns[i]).definition.split(/\s+/)[1],
        field.definition.split(/\s+/)[1], `${t.name}: FK types differ`);
    });
    const uniqueKeys = [
      ...target.fields.filter(f => /PRIMARY KEY|\bUNIQUE\b/.test(f.definition)).map(f => [f.name]),
      ...target.constraints.filter(c => c.startsWith('UNIQUE (')).map(c => cols(c.match(/\(([^)]+)\)/)[1]))
    ];
    assert(uniqueKeys.some(key => key.join(',') === targetColumns.join(',')),
      `${t.name}: FK target ${target.name}(${targetColumns}) is not a declared unique key`);
  }
}
assert(/\bBEGIN;/.test(sql) && /COMMIT;\s*$/.test(sql), 'Missing migration transaction wrapper');
assert(!/^\s*(DROP|DELETE|TRUNCATE)\b/im.test(sql), 'Unexpected destructive statement');
assert(!/<script\b[^>]*\bsrc=/.test(html), 'Unexpected external script dependency');
console.log(`Static design checks PASS: ${tables.length} tables, ${columns} columns, ${foreignKeys} foreign keys; JS syntax and SQL/UI definitions consistent.`);
console.log('No PostgreSQL statements executed. CHECK semantics, permissions, concurrency, migration and recovery still require database integration tests.');
