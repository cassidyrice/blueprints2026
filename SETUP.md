# Card Blueprints API — Deployment Setup

## 1. Google Cloud Setup

### Create a project and enable APIs

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. In **APIs & Services → Library**, search for and enable:
   - **Google Docs API**
   - **Google Drive API**

### Create a service account

1. Go to **IAM & Admin → Service Accounts**.
2. Click **Create Service Account**.
   - Name: `cardblueprints-reader` (or anything you like).
   - Skip optional permissions.
3. On the service account's detail page, go to **Keys → Add Key → Create new key**.
4. Choose **JSON**. A `.json` file downloads — this is your credential file.

### Create a Drive folder and share it

1. In [Google Drive](https://drive.google.com/), create a folder called `Card Blueprints Readings`.
2. Right-click the folder → **Share** → paste the service account email (looks like `cardblueprints-reader@your-project.iam.gserviceaccount.com`) and give it **Editor** access.
3. Open the folder. The URL will look like `https://drive.google.com/drive/folders/XXXXXXXXX` — the `XXXXXXXXX` part is your **GOOGLE_DRIVE_FOLDER_ID**.

## 2. Railway Environment Variables

You need these env vars set on your Railway `web` service:

| Variable | Value |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The **entire contents** of the downloaded JSON key file, pasted as a single-line string |
| `GOOGLE_DRIVE_FOLDER_ID` | The folder ID from the Drive URL |
| `GOOGLE_DOC_SHARE_MODE` | `customer` (shares each doc with the buyer's email) or `public` (anyone-with-link) |
| `GOOGLE_DOC_FALLBACK_INLINE` | `true` (if Google Doc creation fails, email the reading inline instead) |

> **Tip:** On Railway, use `GOOGLE_SERVICE_ACCOUNT_JSON` (the raw JSON string) rather than `GOOGLE_SERVICE_ACCOUNT_FILE` (a file path). File paths are fragile in containerized deploys.

The existing env vars (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`, `RESEND_API_KEY`, `ANTHROPIC_API_KEY`, `FROM_EMAIL`, `BASE_URL`) must also be set.

## 3. Railway CLI Commands

```bash
# Link to your Railway project (run from the repo root)
railway link

# Link to the web service
railway service web

# Set Google Docs variables
railway variables set \
  GOOGLE_SERVICE_ACCOUNT_JSON='<paste entire JSON key file contents here>' \
  GOOGLE_DRIVE_FOLDER_ID='<your-folder-id>' \
  GOOGLE_DOC_SHARE_MODE='customer' \
  GOOGLE_DOC_FALLBACK_INLINE='true'

# Deploy
railway up
```

If you prefer to set variables one at a time:

```bash
railway variables set GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account","project_id":"...","private_key":"...","client_email":"...","...":"..."}'
railway variables set GOOGLE_DRIVE_FOLDER_ID='1aBcDeFgHiJkLmNoPqRsT'
railway variables set GOOGLE_DOC_SHARE_MODE='customer'
railway variables set GOOGLE_DOC_FALLBACK_INLINE='true'
```

## 4. Stripe Payment Link

The payment link for the $20 Cardology Reading is:

```
https://buy.stripe.com/aFa3cvaGe4Ks6iR1fVd3i0n
```

This link is independent of the API — customers pay via this link, Stripe fires the webhook to `/webhook`, the API generates the reading and emails a Google Doc link.

## 5. Cleanup

Delete `__pycache__/` locally if present:

```bash
rm -rf __pycache__
```

The `.gitignore` now excludes `__pycache__/` and `*.pyc`.
