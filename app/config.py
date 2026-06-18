# -*- coding: utf-8 -*-
"""Application Configuration"""
import os


class Config:
    """Base configuration."""
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
