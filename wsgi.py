from app import create_app, DATABASE_URL

application = create_app()
if application.config['PRODUCTION'] and not DATABASE_URL and not application.config['OBJECT_STORAGE']:
    from scripts.backup_scheduler import start
    start()
