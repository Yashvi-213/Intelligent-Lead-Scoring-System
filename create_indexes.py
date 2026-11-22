#!/usr/bin/env python3
"""
Script to create database indexes for better performance
Run this once to optimize your database for large datasets
"""

import sqlite3

DATABASE = 'leads.db'

def create_indexes():
    print("Connecting to database...")
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    cursor = conn.cursor()
    
    print("Enabling performance optimizations...")
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.execute('PRAGMA synchronous=NORMAL')
    cursor.execute('PRAGMA cache_size=10000')
    cursor.execute('PRAGMA temp_store=MEMORY')
    
    print("Creating indexes for faster queries...")
    
    # Create indexes
    indexes = [
        ('idx_status', 'CREATE INDEX IF NOT EXISTS idx_status ON leads(status)'),
        ('idx_predicted_score', 'CREATE INDEX IF NOT EXISTS idx_predicted_score ON leads(predicted_score DESC)'),
        ('idx_status_score', 'CREATE INDEX IF NOT EXISTS idx_status_score ON leads(status, predicted_score DESC)'),
        ('idx_created_at', 'CREATE INDEX IF NOT EXISTS idx_created_at ON leads(created_at DESC)'),
        ('idx_email', 'CREATE INDEX IF NOT EXISTS idx_email ON leads(email)'),
        ('idx_company_name', 'CREATE INDEX IF NOT EXISTS idx_company_name ON leads(company_name)'),
        ('idx_industry', 'CREATE INDEX IF NOT EXISTS idx_industry ON leads(industry)'),
        ('idx_lead_stage', 'CREATE INDEX IF NOT EXISTS idx_lead_stage ON leads(lead_stage)'),
        ('idx_call_status', 'CREATE INDEX IF NOT EXISTS idx_call_status ON leads(call_status)'),
    ]
    
    for idx_name, idx_sql in indexes:
        try:
            cursor.execute(idx_sql)
            print(f"✓ Created index: {idx_name}")
        except Exception as e:
            print(f"✗ Error creating {idx_name}: {e}")
    
    conn.commit()
    
    # Analyze the database for query optimization
    print("\nAnalyzing database for query optimization...")
    cursor.execute('ANALYZE')
    conn.commit()
    
    # Get database stats
    cursor.execute('SELECT COUNT(*) FROM leads')
    total_leads = cursor.fetchone()[0]
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='leads'")
    indexes_list = cursor.fetchall()
    
    conn.close()
    
    print(f"\n{'='*50}")
    print(f"Database optimization complete!")
    print(f"{'='*50}")
    print(f"Total leads: {total_leads:,}")
    print(f"Total indexes: {len(indexes_list)}")
    print(f"\nIndexes created:")
    for idx in indexes_list:
        print(f"  - {idx[0]}")
    print(f"\nYour database is now optimized for fast queries!")
    print(f"{'='*50}")

if __name__ == '__main__':
    create_indexes()
