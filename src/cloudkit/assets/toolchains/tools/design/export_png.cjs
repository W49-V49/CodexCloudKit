// Reuse the prepared Chromium/Playwright installation without file: navigation.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.CLOUDKIT_PLAYWRIGHT_MODULE || 'playwright');
(async()=>{
  const [input,output,requestedScale='2'] = process.argv.slice(2);
  const scale=Number(requestedScale);
  assert.ok(input && output,'Provide checked diagram HTML and PNG output paths');
  assert.ok(Number.isFinite(scale) && scale>=1 && scale<=4,'Scale must be between 1 and 4');
  const browser=await chromium.launch({executablePath:process.env.CLOUDKIT_CHROMIUM_PATH || undefined,headless:true});
  try {
    // Upstream diagrams must convey their complete static meaning without JS.
    const page=await browser.newPage({deviceScaleFactor:scale,javaScriptEnabled:false});
    await page.route('http://**/*',r=>r.abort());await page.route('https://**/*',r=>r.abort());
    await page.setContent(fs.readFileSync(input,'utf8'),{waitUntil:'domcontentloaded'});
    const svg=page.locator('svg').first();
    const dimensions=await svg.evaluate(el=>{
      const box=el.viewBox.baseVal;
      if (![box.width,box.height].every(n=>Number.isFinite(n)&&n>0))throw new Error('A finite positive viewBox is required');
      for(const [axis,size] of [['width',box.width],['height',box.height]])for(const prefix of ['','min-','max-'])el.style.setProperty(prefix+axis,size+'px','important');
      for(let ancestor=el.parentElement;ancestor;ancestor=ancestor.parentElement)ancestor.style.setProperty('overflow','visible','important');
      return {width:box.width,height:box.height};
    });
    await svg.screenshot({path:output,omitBackground:true});
    process.stdout.write(JSON.stringify({exported:true,format:'PNG',scale,viewBox:dimensions,scripts_executed:false,external_fonts_loaded:false,browser:process.env.CLOUDKIT_CHROMIUM_PATH || 'playwright-managed'})+'\n');
  } finally {await browser.close();}
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
