from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, send_file
import pickle
import pandas as pd
import numpy as np
import os
import sqlite3
from datetime import datetime
import pytz
from werkzeug.utils import secure_filename
from io import BytesIO, StringIO

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'

# Set Indian Standard Time
IST = pytz.timezone('Asia/Kolkata')

def get_ist_time():
    """Get current time in IST"""
    return datetime.now(IST)

# Upload configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB for large bulk uploads

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Load the model and preprocessing tools
MODEL_PATH = 'rf_model.pkl'
ENCODER_PATH = 'label_encoders.pkl'
FEATURES_PATH = 'feature_names.pkl'

with open(MODEL_PATH, 'rb') as f:
    model = pickle.load(f)

with open(ENCODER_PATH, 'rb') as f:
    encoders = pickle.load(f)

with open(FEATURES_PATH, 'rb') as f:
    feature_names = pickle.load(f)

# Database setup
DATABASE = 'leads.db'

def init_db():
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    cursor = conn.cursor()
    
    # Enable WAL mode for better concurrent access
    cursor.execute('PRAGMA journal_mode=WAL')
    
    # Performance optimizations
    cursor.execute('PRAGMA synchronous=NORMAL')
    cursor.execute('PRAGMA cache_size=10000')
    cursor.execute('PRAGMA temp_store=MEMORY')
    
    # Check if table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leads'")
    table_exists = cursor.fetchone()
    
    if not table_exists:
        # Create new table with all columns
        cursor.execute('''
            CREATE TABLE leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_name TEXT,
                email TEXT,
                phone TEXT,
                company_name TEXT,
                industry TEXT NOT NULL,
                lead_stage TEXT NOT NULL,
                call_status TEXT NOT NULL,
                call_remarks TEXT NOT NULL,
                users INTEGER NOT NULL,
                predicted_score REAL NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TEXT DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            )
        ''')
        
        # Create table to track deleted IDs for reuse
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS deleted_ids (
                id INTEGER PRIMARY KEY,
                deleted_at TEXT DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            )
        ''')
    else:
        # Check if new columns exist, if not add them
        cursor.execute("PRAGMA table_info(leads)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'contact_name' not in columns:
            cursor.execute('ALTER TABLE leads ADD COLUMN contact_name TEXT')
        if 'email' not in columns:
            cursor.execute('ALTER TABLE leads ADD COLUMN email TEXT')
        if 'phone' not in columns:
            cursor.execute('ALTER TABLE leads ADD COLUMN phone TEXT')
        if 'company_name' not in columns:
            cursor.execute('ALTER TABLE leads ADD COLUMN company_name TEXT')
        if 'notes' not in columns:
            cursor.execute('ALTER TABLE leads ADD COLUMN notes TEXT')
        if 'updated_at' not in columns:
            cursor.execute('ALTER TABLE leads ADD COLUMN updated_at TEXT')
        
        # Create deleted_ids table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS deleted_ids (
                id INTEGER PRIMARY KEY,
                deleted_at TEXT DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            )
        ''')
    
    # Create indexes for faster queries
    try:
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON leads(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_predicted_score ON leads(predicted_score DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_status_score ON leads(status, predicted_score DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON leads(created_at DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_email ON leads(email)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_company_name ON leads(company_name)')
        print("Database indexes created successfully")
    except Exception as e:
        print(f"Index creation note: {e}")
    
    conn.commit()
    conn.close()

def get_next_available_id():
    """Get the next available ID - either from deleted IDs or new"""
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    cursor = conn.cursor()
    
    # Check if there are any deleted IDs available for reuse
    cursor.execute('SELECT id FROM deleted_ids ORDER BY id ASC LIMIT 1')
    deleted_id = cursor.fetchone()
    
    if deleted_id:
        # Reuse the deleted ID
        reused_id = deleted_id[0]
        # Remove it from deleted_ids table
        cursor.execute('DELETE FROM deleted_ids WHERE id = ?', (reused_id,))
        conn.commit()
        conn.close()
        return reused_id
    else:
        # No deleted IDs available, get the next auto-increment ID
        cursor.execute('SELECT MAX(id) FROM leads')
        max_id = cursor.fetchone()[0]
        conn.close()
        return (max_id + 1) if max_id else 1

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    dropdown_data = {k: list(v.classes_) for k, v in encoders.items()}
    
    # Get dashboard statistics
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    cursor = conn.cursor()
    
    # Total leads
    cursor.execute('SELECT COUNT(*) FROM leads')
    total_leads = cursor.fetchone()[0]
    
    # Leads by status
    cursor.execute('SELECT status, COUNT(*) FROM leads GROUP BY status')
    status_counts = dict(cursor.fetchall())
    
    # Average score
    cursor.execute('SELECT AVG(predicted_score) FROM leads')
    avg_score = cursor.fetchone()[0] or 0
    
    # Recent leads (last 5)
    cursor.execute('SELECT * FROM leads ORDER BY created_at DESC LIMIT 5')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM leads ORDER BY created_at DESC LIMIT 5')
    recent_leads = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    dashboard_stats = {
        'total_leads': total_leads,
        'hot_leads': status_counts.get('Hot Lead', 0),
        'warm_leads': status_counts.get('Warm Lead', 0),
        'cold_leads': status_counts.get('Cold Lead', 0),
        'avg_score': round(avg_score, 2),
        'recent_leads': recent_leads
    }
    
    return render_template('index.html', dropdown_data=dropdown_data, stats=dashboard_stats)

def get_dashboard_stats():
    """Helper function to get dashboard statistics"""
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM leads')
    total_leads = cursor.fetchone()[0]
    
    cursor.execute('SELECT status, COUNT(*) FROM leads GROUP BY status')
    status_counts = dict(cursor.fetchall())
    
    cursor.execute('SELECT AVG(predicted_score) FROM leads')
    avg_score = cursor.fetchone()[0] or 0
    
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM leads ORDER BY created_at DESC LIMIT 5')
    recent_leads = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return {
        'total_leads': total_leads,
        'hot_leads': status_counts.get('Hot Lead', 0),
        'warm_leads': status_counts.get('Warm Lead', 0),
        'cold_leads': status_counts.get('Cold Lead', 0),
        'avg_score': round(avg_score, 2),
        'recent_leads': recent_leads
    }

@app.route('/predict', methods=['POST'])
def predict():
    try:
        data = request.form.to_dict()
        force_save = data.get('force_save', 'false') == 'true'
        
        # Extract lead details
        contact_name = data.get('contact_name', '')
        email = data.get('email', '')
        company_name = data.get('company_name', '')
        
        # Check for duplicates if not forcing save
        if not force_save and (email or company_name):
            conn = sqlite3.connect(DATABASE, timeout=30.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            duplicates = []
            
            # Check for email duplicate
            if email:
                cursor.execute('''
                    SELECT id, contact_name, email, company_name, phone, predicted_score, status, created_at
                    FROM leads 
                    WHERE LOWER(email) = LOWER(?)
                ''', (email,))
                email_dups = cursor.fetchall()
                if email_dups:
                    duplicates.extend([dict(row) for row in email_dups])
            
            # Check for company name duplicate
            if company_name:
                cursor.execute('''
                    SELECT id, contact_name, email, company_name, phone, predicted_score, status, created_at
                    FROM leads 
                    WHERE LOWER(company_name) = LOWER(?)
                ''', (company_name,))
                company_dups = cursor.fetchall()
                if company_dups:
                    # Add only if not already in duplicates list
                    existing_ids = [d['id'] for d in duplicates]
                    for dup in company_dups:
                        if dup['id'] not in existing_ids:
                            duplicates.append(dict(dup))
            
            conn.close()
            
            if duplicates:
                # Return duplicate warning with lead data
                dropdown_data = {k: list(v.classes_) for k, v in encoders.items()}
                return render_template('index.html', 
                                     dropdown_data=dropdown_data,
                                     stats=get_dashboard_stats(),
                                     duplicate_warning=True,
                                     duplicates=duplicates,
                                     form_data=data)
        
        # Proceed with prediction
        input_data = {}
        for feat in feature_names:
            val = data.get(feat)
            
            if feat == 'Users':
                input_data[feat] = float(val) if val else 0.0
            elif feat in encoders:
                encoder = encoders[feat]
                try:
                    input_data[feat] = encoder.transform([val])[0]
                except ValueError:
                    input_data[feat] = 0
            else:
                input_data[feat] = val
        
        df = pd.DataFrame([input_data], columns=feature_names)
        
        prediction = model.predict(df)[0]
        score = round(float(prediction), 2)
        
        if score > 75:
            status = "Hot Lead"
            color = "#00ff88"
        elif score > 40:
            status = "Warm Lead"
            color = "#33ccff"
        else:
            status = "Cold Lead"
            color = "#ff3366"
        
        # Save to database with reusable ID
        conn = sqlite3.connect(DATABASE, timeout=30.0)
        cursor = conn.cursor()
        ist_time = get_ist_time().strftime('%Y-%m-%d %H:%M:%S')
        
        # Get next available ID (reused or new)
        next_id = get_next_available_id()
        
        cursor.execute('''
            INSERT INTO leads (id, contact_name, email, phone, company_name, industry, lead_stage, 
                             call_status, call_remarks, users, predicted_score, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (next_id, data.get('contact_name', ''), data.get('email', ''), data.get('phone', ''),
              data.get('company_name', ''), data['Industry'], data['Lead Stage'], 
              data['Call Status'], data['Call Remarks'], int(float(data['Users'])), 
              score, status, data.get('notes', ''), ist_time, ist_time))
        conn.commit()
        conn.close()
        
        flash('Lead saved successfully!', 'success')
        return render_template('result.html', score=score, status=status, color=color, input_data=data)
        
    except Exception as e:
        return render_template('index.html', error=str(e), dropdown_data={k: list(v.classes_) for k, v in encoders.items()}, stats=get_dashboard_stats())

@app.route('/leads')
def view_leads():
    # Get filter parameters
    search = request.args.get('search', '')
    industry_filter = request.args.get('industry', '')
    stage_filter = request.args.get('stage', '')
    status_filter = request.args.get('status', '')
    call_status_filter = request.args.get('call_status', '')
    score_min = request.args.get('score_min', '')
    score_max = request.args.get('score_max', '')
    users_min = request.args.get('users_min', '')
    users_max = request.args.get('users_max', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    sort_by = request.args.get('sort', 'id_asc')  # Default to ID ascending
    
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Build query with filters
    query = 'SELECT * FROM leads WHERE 1=1'
    params = []
    
    if search:
        query += ' AND (contact_name LIKE ? OR email LIKE ? OR company_name LIKE ? OR phone LIKE ?)'
        search_param = f'%{search}%'
        params.extend([search_param, search_param, search_param, search_param])
    
    if industry_filter:
        query += ' AND industry = ?'
        params.append(industry_filter)
    
    if stage_filter:
        query += ' AND lead_stage = ?'
        params.append(stage_filter)
    
    if status_filter:
        query += ' AND status = ?'
        params.append(status_filter)
    
    if call_status_filter:
        query += ' AND call_status = ?'
        params.append(call_status_filter)
    
    if score_min:
        query += ' AND predicted_score >= ?'
        params.append(float(score_min))
    
    if score_max:
        query += ' AND predicted_score <= ?'
        params.append(float(score_max))
    
    if users_min:
        query += ' AND users >= ?'
        params.append(int(users_min))
    
    if users_max:
        query += ' AND users <= ?'
        params.append(int(users_max))
    
    if date_from:
        query += ' AND DATE(created_at) >= ?'
        params.append(date_from)
    
    if date_to:
        query += ' AND DATE(created_at) <= ?'
        params.append(date_to)
    
    # Add sorting with ID as default
    sort_options = {
        'id_asc': 'id ASC',
        'id_desc': 'id DESC',
        'created_desc': 'created_at DESC',
        'created_asc': 'created_at ASC',
        'updated_desc': 'updated_at DESC',
        'updated_asc': 'updated_at ASC',
        'score_desc': 'predicted_score DESC',
        'score_asc': 'predicted_score ASC',
        'name_asc': 'CASE WHEN contact_name IS NULL OR contact_name = "" THEN 1 ELSE 0 END, contact_name COLLATE NOCASE ASC',
        'name_desc': 'CASE WHEN contact_name IS NULL OR contact_name = "" THEN 1 ELSE 0 END, contact_name COLLATE NOCASE DESC',
        'company_asc': 'CASE WHEN company_name IS NULL OR company_name = "" THEN 1 ELSE 0 END, company_name COLLATE NOCASE ASC',
        'company_desc': 'CASE WHEN company_name IS NULL OR company_name = "" THEN 1 ELSE 0 END, company_name COLLATE NOCASE DESC',
        'users_desc': 'users DESC',
        'users_asc': 'users ASC'
    }
    query += f' ORDER BY {sort_options.get(sort_by, "id ASC")}'
    
    cursor.execute(query, params)
    leads = [dict(row) for row in cursor.fetchall()]
    
    # Get unique values for filters
    cursor.execute('SELECT DISTINCT industry FROM leads WHERE industry IS NOT NULL ORDER BY industry')
    industries = [row[0] for row in cursor.fetchall()]
    
    cursor.execute('SELECT DISTINCT lead_stage FROM leads WHERE lead_stage IS NOT NULL ORDER BY lead_stage')
    stages = [row[0] for row in cursor.fetchall()]
    
    cursor.execute('SELECT DISTINCT call_status FROM leads WHERE call_status IS NOT NULL ORDER BY call_status')
    call_statuses = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    
    # Get statistics
    total_leads = len(leads)
    hot_leads = len([l for l in leads if l['status'] == 'Hot Lead'])
    warm_leads = len([l for l in leads if l['status'] == 'Warm Lead'])
    cold_leads = len([l for l in leads if l['status'] == 'Cold Lead'])
    
    stats = {
        'total': total_leads,
        'hot': hot_leads,
        'warm': warm_leads,
        'cold': cold_leads
    }
    
    return render_template('leads.html', 
                         leads=leads, 
                         industries=industries,
                         stages=stages,
                         call_statuses=call_statuses,
                         stats=stats,
                         filters={
                             'search': search,
                             'industry': industry_filter,
                             'stage': stage_filter,
                             'status': status_filter,
                             'call_status': call_status_filter,
                             'score_min': score_min,
                             'score_max': score_max,
                             'users_min': users_min,
                             'users_max': users_max,
                             'date_from': date_from,
                             'date_to': date_to,
                             'sort': sort_by
                         })

@app.route('/lead/<int:lead_id>')
def view_lead(lead_id):
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM leads WHERE id = ?', (lead_id,))
    lead = cursor.fetchone()
    conn.close()
    
    if not lead:
        flash('Lead not found', 'error')
        return redirect(url_for('view_leads'))
    
    return render_template('view_lead.html', lead=dict(lead))

@app.route('/lead/<int:lead_id>/edit', methods=['GET', 'POST'])
def edit_lead(lead_id):
    if request.method == 'GET':
        conn = sqlite3.connect(DATABASE, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM leads WHERE id = ?', (lead_id,))
        lead = cursor.fetchone()
        conn.close()
        
        if not lead:
            flash('Lead not found', 'error')
            return redirect(url_for('view_leads'))
        
        dropdown_data = {k: list(v.classes_) for k, v in encoders.items()}
        return render_template('edit_lead.html', lead=dict(lead), dropdown_data=dropdown_data)
    
    # POST - Update lead
    try:
        data = request.form.to_dict()
        
        # Prepare input for prediction
        input_data = {}
        for feat in feature_names:
            val = data.get(feat)
            
            if feat == 'Users':
                input_data[feat] = float(val) if val else 0.0
            elif feat in encoders:
                encoder = encoders[feat]
                try:
                    input_data[feat] = encoder.transform([val])[0]
                except ValueError:
                    input_data[feat] = 0
            else:
                input_data[feat] = val
        
        df = pd.DataFrame([input_data], columns=feature_names)
        
        # Recalculate prediction
        prediction = model.predict(df)[0]
        score = round(float(prediction), 2)
        
        if score > 75:
            status = "Hot Lead"
        elif score > 40:
            status = "Warm Lead"
        else:
            status = "Cold Lead"
        
        # Update database
        conn = sqlite3.connect(DATABASE, timeout=30.0)
        cursor = conn.cursor()
        ist_time = get_ist_time().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            UPDATE leads 
            SET contact_name = ?, email = ?, phone = ?, company_name = ?,
                industry = ?, lead_stage = ?, call_status = ?, call_remarks = ?, 
                users = ?, predicted_score = ?, status = ?, notes = ?,
                updated_at = ?
            WHERE id = ?
        ''', (data.get('contact_name', ''), data.get('email', ''), data.get('phone', ''),
              data.get('company_name', ''), data['Industry'], data['Lead Stage'], 
              data['Call Status'], data['Call Remarks'], int(float(data['Users'])), 
              score, status, data.get('notes', ''), ist_time, lead_id))
        conn.commit()
        conn.close()
        
        flash('Lead updated successfully!', 'success')
        return redirect(url_for('view_lead', lead_id=lead_id))
        
    except Exception as e:
        flash(f'Error updating lead: {str(e)}', 'error')
        return redirect(url_for('edit_lead', lead_id=lead_id))

@app.route('/bulk-upload', methods=['GET', 'POST'])
def bulk_upload():
    if request.method == 'GET':
        dropdown_data = {k: list(v.classes_) for k, v in encoders.items()}
        return render_template('bulk_upload.html', dropdown_data=dropdown_data)
    
    try:
        if 'file' not in request.files:
            flash('No file uploaded', 'error')
            return redirect(url_for('bulk_upload'))
        
        file = request.files['file']
        
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('bulk_upload'))
        
        if not allowed_file(file.filename):
            flash('Invalid file type. Please upload CSV or Excel file', 'error')
            return redirect(url_for('bulk_upload'))
        
        # Create unique filename with timestamp to avoid conflicts
        original_filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name, ext = os.path.splitext(original_filename)
        filename = f"{name}_{timestamp}{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Ensure upload folder exists
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        
        # Save file
        file.save(filepath)
        print(f"File saved to: {filepath}")
        
        # Verify file exists
        if not os.path.exists(filepath):
            flash(f'Error: Failed to save uploaded file to {filepath}', 'error')
            return redirect(url_for('bulk_upload'))
        
        print(f"File exists: {os.path.exists(filepath)}, Size: {os.path.getsize(filepath)} bytes")
        
        # Read file in chunks for large files
        try:
            if filename.endswith('.csv'):
                # Read CSV to get total rows first
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath)
            
            total_rows = len(df)
            print(f"Successfully read {total_rows} rows from file")
            
            if total_rows > 100000:
                flash(f'File too large ({total_rows} rows). Maximum 100,000 rows allowed.', 'error')
                os.remove(filepath)
                return redirect(url_for('bulk_upload'))
                
        except Exception as read_error:
            if os.path.exists(filepath):
                os.remove(filepath)
            flash(f'Error reading file: {str(read_error)}', 'error')
            return redirect(url_for('bulk_upload'))
        
        # Validate required columns for prediction
        required_columns = feature_names
        missing = [col for col in required_columns if col not in df.columns]
        if missing:
            os.remove(filepath)
            flash(f'Missing required columns: {", ".join(missing)}. Required: Industry, Lead Stage, Call Status, Call Remarks, Users', 'error')
            return redirect(url_for('bulk_upload'))
        
        # Optional columns
        optional_columns = ['contact_name', 'email', 'phone', 'company_name', 'notes']
        for col in optional_columns:
            if col not in df.columns:
                df[col] = ''
        
        # Process in optimized batches
        success_count = 0
        error_count = 0
        duplicate_count = 0
        
        # Configure connection with optimizations for bulk inserts
        conn = sqlite3.connect(DATABASE, timeout=60.0)
        cursor = conn.cursor()
        
        # Maximum performance optimizations
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=OFF')  # Faster but less safe - OK for bulk import
        cursor.execute('PRAGMA cache_size=100000')  # Large cache for big imports
        cursor.execute('PRAGMA temp_store=MEMORY')
        cursor.execute('PRAGMA locking_mode=EXCLUSIVE')
        
        # Pre-fetch max ID
        cursor.execute('SELECT MAX(id) FROM leads')
        max_id = cursor.fetchone()[0] or 0
        next_id = max_id + 1
        
        # Build duplicate check sets for faster lookup (in-memory)
        cursor.execute('SELECT LOWER(email) FROM leads WHERE email IS NOT NULL AND email != ""')
        existing_emails = set(row[0] for row in cursor.fetchall())
        
        cursor.execute('SELECT LOWER(company_name) FROM leads WHERE company_name IS NOT NULL AND company_name != ""')
        existing_companies = set(row[0] for row in cursor.fetchall())
        
        print(f"Loaded {len(existing_emails)} existing emails and {len(existing_companies)} existing companies for duplicate checking")
        
        # Prepare batch insert data
        batch_size = 5000  # Larger batches for better performance
        batch_data = []
        
        ist_time = get_ist_time().strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"Starting to process {total_rows} rows...")
        
        for idx, row in df.iterrows():
            try:
                # Fast duplicate check using in-memory sets
                email = str(row.get('email', '')).strip().lower() if pd.notna(row.get('email', '')) else ''
                company_name = str(row.get('company_name', '')).strip().lower() if pd.notna(row.get('company_name', '')) else ''
                
                if email and email in existing_emails:
                    duplicate_count += 1
                    continue
                
                if company_name and company_name in existing_companies:
                    duplicate_count += 1
                    continue
                
                # Prepare input for prediction
                input_data = {}
                for feat in feature_names:
                    val = row[feat]
                    
                    if pd.isna(val) or val == '':
                        if feat == 'Users':
                            input_data[feat] = 0.0
                        else:
                            raise ValueError(f"Required field '{feat}' is empty")
                        continue
                    
                    if feat == 'Users':
                        input_data[feat] = float(val)
                    elif feat in encoders:
                        encoder = encoders[feat]
                        try:
                            input_data[feat] = encoder.transform([str(val)])[0]
                        except:
                            # Use default on error
                            defaults = {
                                'Industry': 'Other Business Categories',
                                'Lead Stage': 'New',
                                'Call Status': 'Interested',
                                'Call Remarks': 'No Remark'
                            }
                            default_val = defaults.get(feat, list(encoder.classes_)[0])
                            input_data[feat] = encoder.transform([default_val])[0]
                    else:
                        input_data[feat] = val
                
                # Predict score
                pred_df = pd.DataFrame([input_data], columns=feature_names)
                prediction = model.predict(pred_df)[0]
                score = round(float(prediction), 2)
                
                if score > 75:
                    status = "Hot Lead"
                elif score > 40:
                    status = "Warm Lead"
                else:
                    status = "Cold Lead"
                
                # Add to batch
                batch_data.append((
                    next_id,
                    str(row.get('contact_name', '') or ''),
                    str(row.get('email', '') or ''),
                    str(row.get('phone', '') or ''),
                    str(row.get('company_name', '') or ''),
                    str(row['Industry']),
                    str(row['Lead Stage']),
                    str(row['Call Status']),
                    str(row['Call Remarks']),
                    int(float(row['Users'])),
                    score,
                    status,
                    str(row.get('notes', '') or ''),
                    ist_time,
                    ist_time
                ))
                
                # Add to duplicate check sets
                if email:
                    existing_emails.add(email)
                if company_name:
                    existing_companies.add(company_name)
                
                next_id += 1
                success_count += 1
                
                # Batch insert when batch is full
                if len(batch_data) >= batch_size:
                    cursor.executemany('''
                        INSERT INTO leads (id, contact_name, email, phone, company_name, industry, lead_stage, 
                                         call_status, call_remarks, users, predicted_score, status, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', batch_data)
                    conn.commit()
                    print(f"✓ Processed {success_count}/{total_rows} leads ({(success_count/total_rows*100):.1f}%)")
                    batch_data = []
                    
            except Exception as e:
                error_count += 1
                if error_count <= 10:  # Only log first 10 errors
                    print(f"Error processing row {idx + 2}: {e}")
        
        # Insert remaining batch
        if batch_data:
            cursor.executemany('''
                INSERT INTO leads (id, contact_name, email, phone, company_name, industry, lead_stage, 
                                 call_status, call_remarks, users, predicted_score, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', batch_data)
            conn.commit()
        
        # Restore normal settings
        cursor.execute('PRAGMA synchronous=NORMAL')
        cursor.execute('PRAGMA locking_mode=NORMAL')
        
        conn.close()
        
        # Clean up uploaded file
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as cleanup_error:
            print(f"Warning: Could not remove uploaded file: {cleanup_error}")
        
        # Build result message
        message = f'Bulk upload completed! {success_count:,} leads added'
        if duplicate_count > 0:
            message += f', {duplicate_count:,} duplicates skipped'
        if error_count > 0:
            message += f', {error_count:,} errors'
        
        flash(message, 'success' if success_count > 0 else 'error')
        return redirect(url_for('view_leads'))
        
    except Exception as e:
        # Clean up uploaded file on error
        try:
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('bulk_upload'))

@app.route('/delete-lead/<int:lead_id>', methods=['POST'])
def delete_lead(lead_id):
    try:
        conn = sqlite3.connect(DATABASE, timeout=30.0)
        cursor = conn.cursor()
        
        # Store the deleted ID for reuse
        ist_time = get_ist_time().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('INSERT OR IGNORE INTO deleted_ids (id, deleted_at) VALUES (?, ?)', (lead_id, ist_time))
        
        # Delete the lead
        cursor.execute('DELETE FROM leads WHERE id = ?', (lead_id,))
        conn.commit()
        conn.close()
        flash(f'Lead deleted successfully. ID {lead_id} will be reused for the next lead.', 'success')
    except Exception as e:
        flash(f'Error deleting lead: {str(e)}', 'error')
    return redirect(url_for('view_leads'))

@app.route('/api/dashboard-charts')
def dashboard_charts():
    """Get data for dashboard charts"""
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    cursor = conn.cursor()
    
    # Lead Status Distribution
    cursor.execute('SELECT status, COUNT(*) as count FROM leads GROUP BY status')
    status_data = cursor.fetchall()
    
    # Score Distribution (bins)
    cursor.execute('SELECT predicted_score FROM leads')
    scores = [row[0] for row in cursor.fetchall()]
    score_bins = {'0-20': 0, '21-40': 0, '41-60': 0, '61-80': 0, '81-100': 0}
    for score in scores:
        if score <= 20:
            score_bins['0-20'] += 1
        elif score <= 40:
            score_bins['21-40'] += 1
        elif score <= 60:
            score_bins['41-60'] += 1
        elif score <= 80:
            score_bins['61-80'] += 1
        else:
            score_bins['81-100'] += 1
    
    # Top 10 Industries
    cursor.execute('SELECT industry, COUNT(*) as count FROM leads GROUP BY industry ORDER BY count DESC LIMIT 10')
    industry_data = cursor.fetchall()
    
    # Lead Stage Funnel
    cursor.execute('SELECT lead_stage, COUNT(*) as count FROM leads GROUP BY lead_stage ORDER BY count DESC')
    stage_data = cursor.fetchall()
    
    # Users Distribution (bins)
    cursor.execute('SELECT users FROM leads')
    users = [row[0] for row in cursor.fetchall()]
    users_bins = {'0-100': 0, '101-500': 0, '501-1000': 0, '1001-2000': 0, '2000+': 0}
    for user_count in users:
        if user_count <= 100:
            users_bins['0-100'] += 1
        elif user_count <= 500:
            users_bins['101-500'] += 1
        elif user_count <= 1000:
            users_bins['501-1000'] += 1
        elif user_count <= 2000:
            users_bins['1001-2000'] += 1
        else:
            users_bins['2000+'] += 1
    
    # Call Status Overview
    cursor.execute('SELECT call_status, COUNT(*) as count FROM leads GROUP BY call_status ORDER BY count DESC')
    call_status_data = cursor.fetchall()
    
    conn.close()
    
    return jsonify({
        'status': {
            'labels': [row[0] for row in status_data],
            'data': [row[1] for row in status_data]
        },
        'scores': {
            'labels': list(score_bins.keys()),
            'data': list(score_bins.values())
        },
        'industries': {
            'labels': [row[0] for row in industry_data],
            'data': [row[1] for row in industry_data]
        },
        'stages': {
            'labels': [row[0] for row in stage_data],
            'data': [row[1] for row in stage_data]
        },
        'users': {
            'labels': list(users_bins.keys()),
            'data': list(users_bins.values())
        },
        'call_status': {
            'labels': [row[0] for row in call_status_data],
            'data': [row[1] for row in call_status_data]
        }
    })

@app.route('/api/top-leads-by-category')
def top_leads_by_category():
    """Get top 3 leads for each category (Hot, Warm, Cold) - Optimized for large datasets"""
    try:
        conn = sqlite3.connect(DATABASE, timeout=30.0)
        conn.row_factory = sqlite3.Row
        
        # Enable performance optimizations
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA temp_store=MEMORY')
        
        cursor = conn.cursor()
        
        # Optimized query using index - much faster for large datasets
        cursor.execute('''
            SELECT id, contact_name, email, company_name, industry, users, predicted_score, status
            FROM leads 
            WHERE status = 'Hot Lead'
            ORDER BY predicted_score DESC
            LIMIT 3
        ''')
        hot_leads = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('''
            SELECT id, contact_name, email, company_name, industry, users, predicted_score, status
            FROM leads 
            WHERE status = 'Warm Lead'
            ORDER BY predicted_score DESC
            LIMIT 3
        ''')
        warm_leads = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('''
            SELECT id, contact_name, email, company_name, industry, users, predicted_score, status
            FROM leads 
            WHERE status = 'Cold Lead'
            ORDER BY predicted_score DESC
            LIMIT 3
        ''')
        cold_leads = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return jsonify({
            'hot': hot_leads,
            'warm': warm_leads,
            'cold': cold_leads
        })
    except Exception as e:
        print(f"Error in top_leads_by_category: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'hot': [],
            'warm': [],
            'cold': [],
            'error': str(e)
        }), 500

@app.route('/id-pool-stats')
def id_pool_stats():
    """View ID reuse pool statistics"""
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get deleted IDs available for reuse
    cursor.execute('SELECT id, deleted_at FROM deleted_ids ORDER BY id ASC')
    deleted_ids = [dict(row) for row in cursor.fetchall()]
    
    # Get current max ID
    cursor.execute('SELECT MAX(id) as max_id, COUNT(*) as total_leads FROM leads')
    stats = dict(cursor.fetchone())
    
    conn.close()
    
    return jsonify({
        'available_ids': deleted_ids,
        'available_count': len(deleted_ids),
        'max_id': stats['max_id'] or 0,
        'total_leads': stats['total_leads'],
        'next_new_id': (stats['max_id'] or 0) + 1,
        'next_reused_id': deleted_ids[0]['id'] if deleted_ids else None
    })

@app.route('/download-template')
def download_template():
    """Download sample bulk upload template"""
    try:
        return send_file('sample_bulk_upload_template.csv', 
                        as_attachment=True,
                        download_name='sample_bulk_upload_template.csv',
                        mimetype='text/csv')
    except Exception as e:
        flash(f'Error downloading template: {str(e)}', 'error')
        return redirect(url_for('bulk_upload'))

@app.route('/export-leads')
def export_leads():
    """Export filtered leads to CSV"""
    try:
        # Get filter parameters and export format
        export_format = request.args.get('format', 'csv')
        search = request.args.get('search', '')
        industry_filter = request.args.get('industry', '')
        stage_filter = request.args.get('stage', '')
        status_filter = request.args.get('status', '')
        call_status_filter = request.args.get('call_status', '')
        score_min = request.args.get('score_min', '')
        score_max = request.args.get('score_max', '')
        users_min = request.args.get('users_min', '')
        users_max = request.args.get('users_max', '')
        date_from = request.args.get('date_from', '')
        date_to = request.args.get('date_to', '')
        sort_by = request.args.get('sort', 'created_desc')
        
        conn = sqlite3.connect(DATABASE, timeout=30.0)
        
        # Build query with filters (same as view_leads)
        query = 'SELECT * FROM leads WHERE 1=1'
        params = []
        
        if search:
            query += ' AND (contact_name LIKE ? OR email LIKE ? OR company_name LIKE ? OR phone LIKE ?)'
            search_param = f'%{search}%'
            params.extend([search_param, search_param, search_param, search_param])
        
        if industry_filter:
            query += ' AND industry = ?'
            params.append(industry_filter)
        
        if stage_filter:
            query += ' AND lead_stage = ?'
            params.append(stage_filter)
        
        if status_filter:
            query += ' AND status = ?'
            params.append(status_filter)
        
        if call_status_filter:
            query += ' AND call_status = ?'
            params.append(call_status_filter)
        
        if score_min:
            query += ' AND predicted_score >= ?'
            params.append(float(score_min))
        
        if score_max:
            query += ' AND predicted_score <= ?'
            params.append(float(score_max))
        
        if users_min:
            query += ' AND users >= ?'
            params.append(int(users_min))
        
        if users_max:
            query += ' AND users <= ?'
            params.append(int(users_max))
        
        if date_from:
            query += ' AND DATE(created_at) >= ?'
            params.append(date_from)
        
        if date_to:
            query += ' AND DATE(created_at) <= ?'
            params.append(date_to)
        
        # Always sort by ID ascending for exports (regardless of current sort parameter)
        query += ' ORDER BY id ASC'
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if export_format == 'excel':
            # Export to Excel
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Leads', index=False)
            output.seek(0)
            
            return Response(
                output.getvalue(),
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                headers={'Content-Disposition': f'attachment; filename=leads_export_{timestamp}.xlsx'}
            )
        
        elif export_format == 'pdf':
            # Export to PDF with proper text wrapping
            from reportlab.lib.pagesizes import A3, landscape
            from reportlab.lib import colors
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.lib.enums import TA_CENTER, TA_LEFT
            
            output = BytesIO()
            # Use A3 landscape for maximum width
            doc = SimpleDocTemplate(
                output, 
                pagesize=landscape(A3),
                leftMargin=1*cm,
                rightMargin=1*cm,
                topMargin=1*cm,
                bottomMargin=1*cm
            )
            elements = []
            
            # Styles
            styles = getSampleStyleSheet()
            
            # Title style
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=20,
                textColor=colors.HexColor('#1e40af'),
                spaceAfter=12,
                alignment=TA_CENTER
            )
            
            # Cell text styles
            cell_style = ParagraphStyle(
                'CellText',
                parent=styles['Normal'],
                fontSize=8,
                leading=10,
                alignment=TA_LEFT,
                wordWrap='CJK'
            )
            
            cell_center_style = ParagraphStyle(
                'CellCenter',
                parent=styles['Normal'],
                fontSize=8,
                leading=10,
                alignment=TA_CENTER,
                wordWrap='CJK'
            )
            
            # Title
            title = Paragraph(f"<b>Leads Export Report</b><br/><font size=14>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</font>", title_style)
            elements.append(title)
            elements.append(Spacer(1, 0.5*cm))
            
            # Summary stats
            summary_style = ParagraphStyle(
                'Summary',
                parent=styles['Normal'],
                fontSize=12,
                alignment=TA_CENTER,
                spaceAfter=12
            )
            hot_count = len(df[df['status']=='Hot Lead'])
            warm_count = len(df[df['status']=='Warm Lead'])
            cold_count = len(df[df['status']=='Cold Lead'])
            summary_text = f"<b>Total Leads: {len(df)}</b> | Hot: {hot_count} | Warm: {warm_count} | Cold: {cold_count}"
            elements.append(Paragraph(summary_text, summary_style))
            elements.append(Spacer(1, 0.5*cm))
            
            # Prepare data for table with Paragraph objects for proper wrapping
            # Header row
            header_style = ParagraphStyle(
                'Header',
                parent=styles['Normal'],
                fontSize=9,
                leading=11,
                alignment=TA_CENTER,
                textColor=colors.whitesmoke,
                fontName='Helvetica-Bold'
            )
            
            data = [[
                Paragraph('<b>ID</b>', header_style),
                Paragraph('<b>Contact Name</b>', header_style),
                Paragraph('<b>Email</b>', header_style),
                Paragraph('<b>Company Name</b>', header_style),
                Paragraph('<b>Industry</b>', header_style),
                Paragraph('<b>Lead Stage</b>', header_style),
                Paragraph('<b>Call Status</b>', header_style),
                Paragraph('<b>Users</b>', header_style),
                Paragraph('<b>Score</b>', header_style),
                Paragraph('<b>Status</b>', header_style)
            ]]
            
            # Data rows with Paragraph objects for text wrapping
            for _, row in df.iterrows():
                data.append([
                    Paragraph(str(row['id']), cell_center_style),
                    Paragraph(str(row['contact_name'] or 'N/A'), cell_style),
                    Paragraph(str(row['email'] or 'N/A'), cell_style),
                    Paragraph(str(row['company_name'] or 'N/A'), cell_style),
                    Paragraph(str(row['industry']), cell_style),
                    Paragraph(str(row['lead_stage']), cell_style),
                    Paragraph(str(row['call_status']), cell_style),
                    Paragraph(str(row['users']), cell_center_style),
                    Paragraph(str(round(row['predicted_score'], 2)), cell_center_style),
                    Paragraph(str(row['status']), cell_center_style)
                ])
            
            # Column widths for A3 landscape (40cm usable width)
            col_widths = [1.5*cm, 3*cm, 4.5*cm, 5*cm, 3.5*cm, 3*cm, 3*cm, 2*cm, 2*cm, 2.5*cm]
            
            table = Table(data, colWidths=col_widths, repeatRows=1)
            
            # Table style
            table.setStyle(TableStyle([
                # Header styling
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                
                # Data rows styling
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('TOPPADDING', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                
                # Grid
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('LINEBELOW', (0, 0), (-1, 0), 2, colors.HexColor('#1e40af')),
                
                # Alternating row colors
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f3f4f6')]),
            ]))
            
            elements.append(table)
            
            # Build PDF
            try:
                doc.build(elements)
                output.seek(0)
                
                return Response(
                    output.getvalue(),
                    mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename=leads_export_{timestamp}.pdf'}
                )
            except Exception as pdf_error:
                print(f"PDF generation error: {pdf_error}")
                import traceback
                traceback.print_exc()
                flash(f'Error generating PDF: {str(pdf_error)}', 'error')
                return redirect(url_for('view_leads'))
        
        else:
            # Export to CSV (default)
            output = StringIO()
            df.to_csv(output, index=False)
            output.seek(0)
            
            return Response(
                output.getvalue(),
                mimetype='text/csv',
                headers={'Content-Disposition': f'attachment; filename=leads_export_{timestamp}.csv'}
            )
        
    except Exception as e:
        flash(f'Error exporting leads: {str(e)}', 'error')
        return redirect(url_for('view_leads'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
