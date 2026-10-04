from app import create_app

application = create_app()
if application.config['PRODUCTION']:
    from scripts.backup_scheduler import start
    start()
