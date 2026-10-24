// Actual local-browser rendering proof; no provider/model credentials or uploads.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.CLOUDKIT_PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const [input, output] = process.argv.slice(2);
  const browser = await chromium.launch({executablePath:process.env.CLOUDKIT_CHROMIUM_PATH || undefined,headless:true});
  const checks = [];
  try {
    for (const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
      const page = await browser.newPage({viewport});
      await page.route('http://**/*',r=>r.abort());
      await page.route('https://**/*',r=>r.abort());
      // The managed browser rejects file: navigation. Load the already checked
      // local fixture directly instead of changing that platform policy.
      await page.setContent(fs.readFileSync(input,'utf8'),{waitUntil:'domcontentloaded'});
      const actual = await page.locator('svg').first().evaluate(el=>({role:el.getAttribute('role'),label:el.getAttribute('aria-labelledby'),width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height,bodyWidth:document.body.scrollWidth,viewport:window.innerWidth,svgCount:document.querySelectorAll('svg').length}));
      assert.equal(actual.role,'img');assert.ok(actual.label);assert.ok(actual.width>0 && actual.height>0);
      assert.ok(actual.bodyWidth<=actual.viewport+1,`Page-level horizontal overflow at ${viewport.width}: ${actual.bodyWidth}`);
      checks.push({viewport,passed:true,...actual});
      await page.close();
    }
  } finally {await browser.close();}
  const report={passed:true,input_method:'setContent from local pinned fixture; file URL policy unchanged',scope:'Actual Chromium rendered the pinned upstream static example at desktop/mobile widths; external requests aborted, no model or business design acceptance',checks};
  fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
  process.stdout.write(JSON.stringify({passed:true,viewports:checks.length})+'\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
