# 🤫 Stealth Confession Bot & Command Center

This project has been massively condensed into exactly **3 files** for seamless online hosting:
1. `app.py`: The unified backend (Discord Bot + Web Dashboard).
2. `index.html`: The entire frontend (SPA structure) including all styles and scripts.
3. `README.md`: These deployment instructions.

*(Note: A `requirements.txt` is also included, as this is strictly required by online hosting providers to install dependencies).*

---

## ☁️ Step 1: Set up Supabase (Database)

We've migrated from local SQLite to **Supabase PostgreSQL** for robust cloud hosting.

1. Go to [Supabase](https://supabase.com/) and create a new project.
2. Go to **Project Settings > Database** (or the connection string section) and copy your **Connection String (URI)**. It should look like `postgresql://postgres.[ref]:[password]@aws-0-REGION.pooler.supabase.com:6543/postgres`.
3. Open your local `.env` file (create one if it doesn't exist) and paste the connection string:
   ```env
   DATABASE_URL="postgresql://postgres.[ref]:[password]@aws-0-REGION.pooler.supabase.com:6543/postgres"
   ```
4. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Run the initialization script to automatically create all required tables and insert the default configuration/admins:
   ```bash
   python init_db.py
   ```
   *(This will create your tables and insert the default admins `byte` and `tuktuk` with the password `byte123`).*

---

## 🚀 Step 2: Deploy to Production (Render / Railway)

1. Connect your GitHub repository to your hosting provider.
2. Use the following start command:
   ```bash
   python app.py
   ```
3. **Environment Variables**: You MUST set the following in your hosting provider's dashboard:

| Variable | Description |
|---|---|
| `DATABASE_URL` | Your Supabase connection string from Step 1. |
| `BOT_TOKEN` | Your Discord Bot Token. |
| `SECRET_PATH` | The secret URL path for your dashboard (e.g., `cmd-9x4k2`). |
| `FLASK_SECRET` | A random string (e.g., `my-super-secret-key-123`). |

*(Note: `GUILD_ID`, `PUBLIC_CHANNEL_ID`, and `ADMIN_CHANNEL_ID` can still be set as environment variables as a fallback, but it is highly recommended to set them dynamically in the Web Dashboard Settings).*

---

## 🔑 Step 3: Login & Manage
Once deployed, go to your hosted domain followed by your secret path:
👉 `https://your-app.onrender.com/cmd-9x4k2`

- **Master Account:** Username `byte` | Password `byte123`
- *Please change your password immediately in the Admin Manager tab!*

---

## 🤖 Step 4: Discord Bot Setup (Channel Hosting)

To make the bot function in a specific server and send confessions to a particular channel, you need to configure your server IDs. You can now do this directly from the **God's Dashboard** without needing to change environment variables!

1. Log into the web dashboard using your master/god account.
2. Navigate to **Settings** -> **Server Setup**.
3. **Guild ID (Server ID)**: 
   - Open Discord, go to User Settings > Advanced, and enable "Developer Mode".
   - Right-click your Server icon and select "Copy Server ID".
   - Paste it into the Guild ID field. This ensures your `/confess` command syncs instantly to your server.
4. **Public Confession Channel ID**: 
   - Right-click the specific channel where you want the bot to post approved confessions (e.g., `#anonymous-confessions`) and select "Copy Channel ID".
   - Paste it into the Public Confession Channel ID field.
5. **Admin Notifications Channel ID** (Optional):
   - Right-click your private admin channel and select "Copy Channel ID". The bot will send security leak alerts here if anyone tries to brute-force your dashboard.
6. Click **Save Server Settings**.

*Note: If the `/confess` command doesn't appear immediately after setting the Guild ID, simply restart your bot application to trigger an immediate command sync.*
