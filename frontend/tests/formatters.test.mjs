import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/formatters.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const module = { exports: {} };
vm.runInNewContext(compiled, { exports: module.exports, module, Intl, Date, Number, Math });
const { formatINR, formatIndianNumber, formatIndianDate, formatIndianDateTime } = module.exports;

test('currency uses INR and Indian lakh grouping', () => {
  assert.equal(formatINR(49), '₹49');
  assert.equal(formatINR(1499), '₹1,499');
  assert.equal(formatINR(12500), '₹12,500');
  assert.equal(formatINR(125000), '₹1,25,000');
  assert.equal(formatINR(1250000), '₹12,50,000');
  assert.equal(formatINR(49.5), '₹49.5');
  assert.equal(formatIndianNumber(1234567), '12,34,567');
});

test('dates use DD/MM/YYYY and convert UTC timestamps to IST', () => {
  assert.equal(formatIndianDate('2026-11-24'), '24/11/2026');
  assert.equal(formatIndianDateTime('2026-11-24T12:00:00Z'), '24/11/2026, 05:30 pm');
  assert.equal(formatIndianDateTime(undefined), '—');
});
