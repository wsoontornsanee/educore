import assert from 'node:assert';
import { existsSync, readFileSync } from 'node:fs';
import { describe, it } from 'node:test';

const readJson = (rel: string) => JSON.parse(readFileSync(new URL(`../${rel}`, import.meta.url), 'utf8'));

const app = readJson('app.json').expo;
const pkg = readJson('package.json');
const eas = readJson('eas.json');

describe('release config', () => {
  it('keeps app.json version as strict semver, mirrored in package.json', () => {
    assert.match(app.version, /^\d+\.\d+\.\d+$/);
    assert.strictEqual(pkg.version, app.version);
  });

  it('leaves build numbers to EAS remote versioning', () => {
    assert.strictEqual(eas.cli.appVersionSource, 'remote');
    assert.strictEqual(eas.build.production.autoIncrement, true);
    assert.strictEqual(app.android.versionCode, undefined);
    assert.strictEqual(app.ios.buildNumber, undefined);
  });

  it('points every build profile at a non-loopback https API', () => {
    for (const name of ['preview', 'production']) {
      assert.match(eas.build[name].env.EXPO_PUBLIC_API_URL, /^https:\/\/(?!localhost|10\.0\.2\.2)/, name);
    }
  });

  it('ships every asset app.json references, and the entry module', () => {
    const paths = [
      app.icon,
      app.android.adaptiveIcon.foregroundImage,
      app.plugins.find((p: unknown) => Array.isArray(p) && p[0] === 'expo-notifications')[1].icon,
      pkg.main,
    ];
    for (const rel of paths) {
      assert.ok(existsSync(new URL(`../${rel}`, import.meta.url)), rel);
    }
  });
});
