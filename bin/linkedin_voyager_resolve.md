# LinkedIn offsite-apply resolution via the voyager API

The reliable way to turn a LinkedIn job into an applyable ATS job. LinkedIn's
own authenticated API returns the exact employer apply URL (or an Easy-Apply
flag) for every posting. Ground truth, no company/title guessing, no false
matches. Reading only, from the user's own logged-in session, so it carries none
of the account risk that submitting through LinkedIn does.

## Why not Firecrawl search
Firecrawl refuses linkedin.com, and resolving company+title through web search
hit only ~15% and mis-matched (Jerry -> otter's Greenhouse board). The voyager
API resolves ~95% correctly. Keep `bin/resolve_linkedin.py` (company-guarded)
as the no-login automated fallback in the cycle; use this for the real work.

## Run it (needs the logged-in Playwright profile)

1. Collect the job ids to resolve, value-ordered:
   ```
   .venv/bin/python - <<'PY'
   import sqlite3,json,re
   c=sqlite3.connect('jobs.db'); c.row_factory=sqlite3.Row
   pri={'queued':0,'scored':1,'discovered':2,'needs_jd':3}
   rows=[]
   for r in c.execute("select id,url,status,coalesce(score,0) sc from jobs "
                      "where ats='linkedin' and status in "
                      "('scored','queued','discovered','needs_jd') "
                      "and url like '%/jobs/view/%'"):
       m=re.search(r'/jobs/view/(\d+)', r['url'])
       if m: rows.append({'jid':m.group(1),'p':pri[r['status']],'sc':r['sc']})
   rows.sort(key=lambda x:(x['p'],-x['sc']))
   json.dump(rows, open('/tmp/li_ids.json','w'))
   print(len(rows))
   PY
   ```

2. In the logged-in browser (Playwright MCP on the main profile), run this
   fetch loop for a batch of up to ~90 ids. It calls LinkedIn's own API with
   the session cookie, so it must run in-page:
   ```js
   async () => {
     const ids = [/* batch of numeric jids */];
     const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)/)||[])[1]||'';
     const out = [];
     for (const id of ids) {
       try {
         const r = await fetch(`https://www.linkedin.com/voyager/api/jobs/jobPostings/${id}?decorationId=com.linkedin.voyager.deco.jobs.web.shared.WebFullJobPosting-65`,
           {headers:{'csrf-token':csrf,'accept':'application/json'}});
         if (!r.ok) { out.push({jid:id, err:r.status}); await new Promise(x=>setTimeout(x,300)); continue; }
         const j = await r.json();
         const am = j.applyMethod || {};
         const off = am['com.linkedin.voyager.jobs.OffsiteApply'];
         const easy = am['com.linkedin.voyager.jobs.ComplexOnsiteApply'];
         out.push({jid:id, offsite: off ? off.companyApplyUrl : null, easy: !!easy});
       } catch(e) { out.push({jid:id, err:String(e).slice(0,40)}); }
       await new Promise(x=>setTimeout(x,300));   // be gentle
     }
     return JSON.stringify(out);
   }
   ```

3. Save that JSON array to a file and apply it:
   ```
   .venv/bin/python bin/apply_li_resolution.py results.json
   ```
   Offsite URLs on a known ATS become first-class `discovered` jobs the fleet
   applies to directly. Easy-Apply-only jobs become `needs_human` with a note,
   for a throttled logged-in pass. `applied`/`applying` rows are never touched.

## Easy-Apply-only jobs
About 10% of LinkedIn postings are Easy-Apply-only (no employer URL). Those
must be submitted through the logged-in session and cannot be a 24/7
autonomous fleet without risking the account. Handle them in small throttled
batches from an interactive session; they are queued as `needs_human` with the
note "linkedin easy apply only".
