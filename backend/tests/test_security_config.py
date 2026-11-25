from app.config import production_errors


def secure_environment():
    return {
        'APP_ENV':'production',
        'DATABASE_URL':'postgresql+psycopg://portfolio:unique-rotated-password@db.example.test:5432/retail?sslmode=verify-full',
        'COOKIE_SECURE':'true',
        'CORS_ORIGINS':'https://retail.example.test,https://ops.example.test',
        'DEBUG':'false',
    }


def test_production_configuration_accepts_explicit_secure_settings():
    assert production_errors(secure_environment()) == []


def test_production_configuration_rejects_demo_database_settings_without_echoing_values():
    env=secure_environment()
    env['DATABASE_URL']='postgresql+psycopg://retail:retail@localhost:5432/retail'
    env['COOKIE_SECURE']='false'
    env['CORS_ORIGINS']='*'
    env['DEBUG']='true'
    errors=production_errors(env)
    assert any('demo password' in error for error in errors)
    assert any('COOKIE_SECURE' in error for error in errors)
    assert any('CORS_ORIGINS' in error for error in errors)
    assert any('sslmode=verify-full' in error for error in errors)
    assert any('DEBUG' in error for error in errors)
    assert 'retail' not in ' '.join(errors)

def test_production_configuration_rejects_example_password_and_invalid_session_lifetime():
    env=secure_environment()
    env['DATABASE_URL']='postgresql+psycopg://portfolio:replace-with-a-unique-local-password@db.example.test:5432/retail?sslmode=verify-full'
    env['SESSION_HOURS']='0'
    errors=production_errors(env)
    assert any('demo password' in error for error in errors)
    assert any('SESSION_HOURS' in error for error in errors)


def test_production_configuration_rejects_missing_or_non_postgresql_database_url():
    env=secure_environment();env['DATABASE_URL']='sqlite:///unsafe.db'
    assert any('PostgreSQL' in error for error in production_errors(env))
    env.pop('DATABASE_URL')
    assert production_errors(env)==['DATABASE_URL is required in production']


def test_development_configuration_keeps_local_fallbacks():
    assert production_errors({'APP_ENV':'development'})==[]
