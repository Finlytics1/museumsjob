# Museumsjob København

**👉 See today's jobs: [finlytics1.github.io/museumsjob](https://finlytics1.github.io/museumsjob/)**

A daily search for part-time student jobs in the Copenhagen area for **etnologi** and **antropologi** students, with a focus on museums: presenting to an audience (formidling), helping with research, building exhibitions, or just working at a museum.

The search runs automatically every morning on GitHub, updates the website, and sends an email with the results.

## What the colours mean

| Colour | Meaning |
|---|---|
| 🩷 **Pink** | Formidling, forskning or udstilling: presenting, research or exhibition work. Shown first. |
| 🟢 **Green** | Paid job (lønnet) |
| 🔴 **Red** | Unpaid: internship or volunteer work (ulønnet) |

Pink jobs also carry a green or red badge, so you can still see whether they're paid.

Each job also shows:
- **Hours**, when the ad states them, e.g. `15 t/uge` or `10-15 t/måned`
- **Application deadline** (frist)
- **NY**, if the job wasn't there in the previous run

## What gets filtered out

- Jobs that aren't museum work and don't mention etnologi or antropologi
- Jobs that require a finished master's (kandidat) or PhD, plus senior titles like museumsinspektør, postdoc or leder
- Full-time jobs, including any job stating 30 or more hours a week
- Jobs outside Storkøbenhavn and Nordsjælland

Jobs open to bachelor students, or to anyone, are kept.

## Sources

- [Jobindex.dk](https://www.jobindex.dk): student jobs, internships, volunteer and hourly-paid jobs
- [frivilligjob.dk](https://frivilligjob.dk): volunteer roles in culture. **Only when run locally**, because the site blocks GitHub's servers.

## How it runs

The GitHub Actions workflow in [`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs every day at 06:00 UTC (08:00 Danish summer time, 07:00 winter time). Each run:

1. Searches the job sites and reads every relevant ad in full
2. Emails a colour-coded summary, with the full report attached
3. Publishes the report to the website
4. Saves which jobs it has seen (`seen.json`), so new ones can be marked **NY**

To run it now, go to **Actions → Daily museumsjob search → Run workflow**.

### Email settings

The email is sent through Gmail. It's set up with three repository secrets under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `EMAIL_FROM` | The Gmail address that sends the email |
| `EMAIL_TO` | Who receives it. Separate several addresses with commas. |
| `EMAIL_APP_PASSWORD` | A [Gmail app password](https://myaccount.google.com/apppasswords) for `EMAIL_FROM` |

## Running it on your own computer

```bash
pip install -r requirements.txt
python museumsjob.py
```

This prints the jobs in colour and opens the report in your browser.

| Option | What it does |
|---|---|
| `--area all` | Also search Region Sjælland |
| `--show-rejected` | List the filtered-out jobs and why each was removed |
| `--email` | Send the email too. Copy `email_config.example.json` to `email_config.json` and fill it in first. |
| `--no-open` | Write the report without opening the browser |
