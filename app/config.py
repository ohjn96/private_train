# -*- coding: utf-8 -*-
"""Application Configuration"""
import os


class Config:
    """Base configuration."""
    # 실제 키는 create_app 이 정한다 (환경변수 또는 무작위 생성 파일)
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY')
    DEBUG = False
    TESTING = False


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
