from app import create_app

application = create_app()
if application.config['PRODUCTION'] and not application.config['OBJECT_STORAGE']:
    from scripts.backup_scheduler import start
    start()
