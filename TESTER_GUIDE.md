# WA Outreach — Install Guide

---

## Mac

### Step 1 — Download & unzip
1. Download `WA_Outreach_Mac.zip` from the link you were sent
2. Go to your **Downloads** folder and double-click `WA_Outreach_Mac.zip` to unzip it
3. You will see a file called **WA Outreach.app** appear
4. Drag **WA Outreach.app** into your **Applications** folder (open Finder → click Applications on the left sidebar → drag the app in)

---

### Step 2 — First launch (you must do this every new version)

When you double-click the app for the first time, macOS will block it and show a message like **"WA Outreach Not Opened"** or **"Apple could not verify…"**. This happens because the app is not yet registered with Apple. It is safe — follow the steps below to open it.

**Do NOT click "Move to Trash."** Click **Done** to dismiss that popup, then follow these steps:

#### Option A — Using Terminal (recommended, takes 30 seconds)

1. Open **Terminal**
   - Press **Command (⌘) + Space** on your keyboard to open Spotlight Search
   - Type **Terminal** and press Enter
   - A black window opens — that is Terminal

2. Copy and paste this command into Terminal, then press **Enter**:
   ```
   xattr -cr "/Applications/WA Outreach.app"
   ```
   *(Nothing will appear after you press Enter — that is normal)*

3. Close Terminal

4. Go to your **Applications** folder and double-click **WA Outreach** — it will open normally

#### Option B — Using System Settings

1. Try to open the app once (double-click it) so macOS registers the blocked attempt
2. Open **System Settings** → **Privacy & Security**
3. Scroll down until you see a message about **"WA Outreach" was blocked**
4. Click **Open Anyway**
5. Enter your Mac password if asked
6. The app will open

---

### Step 3 — First run note
The first time the app opens, it downloads a small browser component in the background (~150 MB). This only happens once. The app is usable while it downloads — you only need the browser when you connect a WhatsApp number.

---

## Windows

### Step 1 — Download & unzip
1. Download `WA_Outreach_Windows.zip` from the link you were sent
2. Go to your **Downloads** folder, right-click `WA_Outreach_Windows.zip` and click **Extract All**
3. Choose where to save it (e.g. your Desktop or `C:\WA Outreach\`) and click **Extract**
4. Open the extracted folder and double-click **WA Outreach.exe**

---

### Step 2 — First launch (you must do this every new version)

Windows may show a blue screen saying **"Windows protected your PC"**. This is normal for apps that are not yet registered with Microsoft. Follow these steps:

1. Click **More info** (small link in the middle of the blue screen)
2. Click **Run anyway**
3. The app will open

---

### Step 3 — If the window never opens (WebView2 missing)
The app needs a component called **Microsoft WebView2** to display its window. It comes pre-installed on Windows 11 and most up-to-date Windows 10 machines. If the app launches but no window appears:

1. Go to: https://developer.microsoft.com/microsoft-edge/webview2/
2. Download the **Evergreen Bootstrapper** (~2 MB)
3. Run it and follow the prompts
4. Open **WA Outreach.exe** again

---

### Step 4 — First run note
Same as Mac — the app downloads a browser component (~150 MB) once in the background on first launch.

---

## Using the app

1. **Sign in** with your `@drivenproperties.com` email
   - If you are the first person signing in, create an account — it will automatically be set as admin
2. **Numbers** → Add a WhatsApp number → scan the QR code with your phone (exactly like WhatsApp Web)
3. **Campaign** → Import your contacts file → Start Sending
4. **Inbox** → See replies from contacts and reply back

---

## Importing contacts

Download the template here: https://raw.githubusercontent.com/Keeksawy/Wtsp-blast/main/public/contact_template.csv

Open it in Excel or Google Sheets and fill in your contacts starting from **row 2**. Do not rename the column headers.

| Column | Required | What it does |
|---|---|---|
| Owner Name | Yes | Fills in the contact's name in the message |
| Mobile Number | Yes | The number the message is sent to |
| Unit Number | Yes | Fills in the property unit in the message |
| Campaign Name | No | Groups contacts together in reports |
| Sales Agent ID | No | Tags the contact to an agent in your CRM |

> **Mobile numbers must include the country code.** For UAE numbers, start with **+971** (e.g. +971501234567). Numbers without a country code will be skipped.

---

## Feedback
Send any issues or feedback to **kareem.mazhar@drivenproperties.com**
