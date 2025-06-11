from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, url_for, session
import os
import time
from collections import defaultdict, deque
import requests
from subprocess import Popen
import mysql.connector
from mysql.connector import Error
from werkzeug.security import check_password_hash
from functools import wraps
import threading

app = Flask(__name__)
app.secret_key = 'segredo_super_secreto'

# Database connection configuration
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Paulinha02',
    'database': 'sistema_dados',
    'port': 3306
}

# Store last 5 values per generator
data_by_generator = defaultdict(lambda: deque(maxlen=5))

# Global flag to control the monitoring thread
monitoring_active = True

def get_db_connection():
    """Establish database connection"""
    try:
        connection = mysql.connector.connect(**db_config)
        return connection
    except Error as e:
        print(f"Database connection error: {e}")
        return None

def execute_query(query, params=None, fetch_all=True, commit=False, dictionary=True):
    """Execute a database query with proper error handling"""
    connection = get_db_connection()
    if not connection:
        return None
    
    try:
        cursor = connection.cursor(dictionary=dictionary)
        cursor.execute(query, params or ())
        
        result = None
        if fetch_all:
            result = cursor.fetchall()
        elif not commit:
            result = cursor.fetchone()
        
        if commit:
            connection.commit()
            result = cursor.lastrowid or True
            
        cursor.close()
        connection.close()
        return result
    except Error as e:
        print(f"Database error: {e}")
        if connection:
            connection.close()
        return None

def load_last_five_from_database():
    query = """
    SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY sensor_id ORDER BY timestamp DESC) as rn
        FROM dados
    ) ranked
    WHERE rn <= 5
    ORDER BY timestamp
    """
    
    results = execute_query(query)
    if results:
        for row in results:
            generator_id = row['sensor_id']
            data_by_generator[generator_id].append({
                "timestamp": row['timestamp'].strftime("%Y-%m-%d %H:%M:%S"),
                "sensor_id": generator_id,
                "temperature": row['temperature'],
                "humidity": row['humidity'],
                "CO2": row['CO2'],
                "Lum": row['Lum']
            })

def get_all_sensors():
    return execute_query("SELECT id FROM sistemas_sensores ORDER BY id") or []

def get_user_sensors(user_id):
    query = """
    SELECT s.id 
    FROM sistemas_sensores s
    JOIN utilizadores_sensores us ON s.id = us.id_sensor
    WHERE us.id_utilizador = %s
    ORDER BY s.id
    """
    return execute_query(query, (user_id,)) or []

def get_user_id(username):
    query = "SELECT id FROM utilizadores WHERE nome = %s"
    result = execute_query(query, (username,), fetch_all=False)
    return result['id'] if result else None

def get_next_sensor_id():
    query = "SELECT MAX(id) as max_id FROM sistemas_sensores"
    result = execute_query(query, fetch_all=False)
    return (result['max_id'] or 0) + 1 if result else 1

def create_new_sensor():
    next_id = get_next_sensor_id()
    result = execute_query("INSERT INTO sistemas_sensores (id) VALUES (%s)", (next_id,), commit=True)
    return next_id if result else False

def get_user_associated_sensors(user_id):
    return get_user_sensors(user_id)

def get_unassociated_sensors(user_id):
    query = """
    SELECT s.id 
    FROM sistemas_sensores s
    WHERE s.id NOT IN (
        SELECT id_sensor 
        FROM utilizadores_sensores 
        WHERE id_utilizador = %s
    )
    ORDER BY s.id
    """
    return execute_query(query, (user_id,)) or []

def get_all_users():
    query = "SELECT id, nome FROM utilizadores WHERE permissao = 'user' ORDER BY nome"
    return execute_query(query) or []

def monitor_database():
    while monitoring_active:
        try:
            query = """
            SELECT * FROM dados
            ORDER BY timestamp DESC
            LIMIT 5
            """
            results = execute_query(query)
            
            if results:
                for row in results:
                    sensor_id = row['sensor_id']
                    new_entry = {
                        "timestamp": row['timestamp'].strftime("%Y-%m-%d %H:%M:%S"),
                        "sensor_id": sensor_id,
                        "temperature": row['temperature'],
                        "humidity": row['humidity'],
                        "CO2": row['CO2'],  
                        "Lum": row['Lum']  
                    }
                    
                    # Check if this entry already exists
                    if not any(entry['timestamp'] == new_entry['timestamp'] 
                              for entry in data_by_generator[sensor_id]):
                        data_by_generator[sensor_id].append(new_entry)
            
            # Sleep between checks for efficiency
            time.sleep(1)
        except Exception as e:
            print(f"Database monitoring error: {e}")
            time.sleep(1)

# Authentication decorator
def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('login'))
            if role and session.get('permissao') != role:
                return "Acesso negado! Não tens permissão para esta página.", 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def check_sensor_access(sensor_id):
    if session.get('permissao') == 'admin':
        return True
        
    user_id = session.get('user_id')
    if not user_id:
        return False
        
    user_sensors = get_user_sensors(user_id)
    user_sensor_ids = [sensor['id'] for sensor in user_sensors]
    
    return int(sensor_id) in user_sensor_ids

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# HTTP Routes
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nome = request.form['nome']
        password = request.form['password']
        
        query = "SELECT * FROM utilizadores WHERE nome = %s"
        user = execute_query(query, (nome,), fetch_all=False)
        
        if user and user['password'] == password:
            session['user'] = user['nome']
            session['user_id'] = user['id']
            session['permissao'] = user['permissao']
            return redirect(url_for('admin_dashboard' if user['permissao'] == 'admin' else 'user'))
        else:
            return "Credenciais inválidas. Tente novamente."
    
    return render_template('login.html')

@app.route('/sensores', methods=['GET', 'POST'])
@login_required('admin')
def pagina_sensores():
    if request.method == 'POST':
        create_new_sensor()

    sensores = get_all_sensors()
    utilizadores = get_all_users()
    
    utilizadores_sensores = {}
    for user in utilizadores:
        utilizadores_sensores[user['id']] = {
            'associated_sensors': get_user_associated_sensors(user['id']),
            'unassociated_sensors': get_unassociated_sensors(user['id'])
        }

    return render_template("sensores.html", 
                          sensores=sensores, 
                          utilizadores=utilizadores,
                          utilizadores_sensores=utilizadores_sensores)

@app.route('/utilizadores', methods=['GET'])
@login_required('admin')
def pagina_utilizadores():
    utilizadores = get_all_users()
    return render_template("utilizadores.html", utilizadores=utilizadores)

@app.route('/admin-dashboard')
@login_required('admin')
def admin_dashboard():
    return render_template("admin_dashboard.html")

@app.route('/associar_utilizador_sensor', methods=['POST'])
@login_required('admin')
def associar_utilizador_sensor():
    id_utilizador = request.form.get('id_utilizador')
    id_sensor = request.form.get('id_sensor')

    if not id_utilizador or not id_sensor:
        return "Faltam dados", 400

    try:
        response = requests.post(
            "http://127.0.0.1:5000/associate_user_sensor",
            json={"user_id": id_utilizador, "sensor_id": id_sensor},
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 200:
            return redirect(url_for('pagina_sensores'))
        else:
            return f"Erro ao associar: {response.text}", 500
    except requests.RequestException as e:
        return f"Erro de conexão: {str(e)}", 500
    
@app.route('/remover_associacao_utilizador_sensor', methods=['POST'])
@login_required('admin')
def remover_associacao_utilizador_sensor():
    id_utilizador = request.form.get('id_utilizador')
    id_sensor = request.form.get('id_sensor')

    if not id_utilizador or not id_sensor:
        return "Faltam dados", 400

    try:
        response = requests.post(
            "http://127.0.0.1:5000/disassociate_user_sensor",
            json={"user_id": id_utilizador, "sensor_id": id_sensor},
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 200:
            return redirect(url_for('pagina_sensores'))
        else:
            return f"Erro ao remover associação: {response.text}", 500
    except requests.RequestException as e:
        return f"Erro de conexão: {str(e)}", 500

@app.route('/user')
@login_required('user')  
def user():
    user_id = session.get('user_id')
    user_sensors = get_user_sensors(user_id)
    return render_template('home.html', nome=session['user'], sensores=user_sensors)

@app.route('/criar_utilizador', methods=['POST'])
@login_required('admin')
def criar_utilizador():
    nome = request.form.get('nome')
    password = request.form.get('password')
    permissao = request.form.get('permissao')

    if not nome or not password or not permissao:
        return "Faltam dados", 400

    try:
        response = requests.post(
            "http://127.0.0.1:5000/create_user",
            json={
                "nome": nome, 
                "password": password, 
                "permissao": permissao
            },
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 200:
            return redirect(url_for('pagina_utilizadores'))
        else:
            return f"Erro ao criar utilizador: {response.text}", 500
    
    except requests.RequestException as e:
        return f"Erro de conexão: {str(e)}", 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/start", methods=['GET'])
def display_start_page():
    """Page to choose generator ID to start"""
    if session.get('permissao') == 'admin':
        sensores = get_all_sensors()
    else:
        user_id = session.get('user_id')
        sensores = get_user_sensors(user_id)
    
    return render_template("start.html", sensores=sensores)

@app.route("/create-sensor", methods=['POST'])
@login_required('admin')
def create_sensor():
    """Create a new sensor"""
    new_sensor_id = create_new_sensor()
    if not new_sensor_id:
        return jsonify({"error": "Falha ao criar sensor"}), 500

    return redirect(url_for('pagina_sensores'))

@app.route("/home", methods=['GET'])
def display_home_page():
    if session.get('permissao') == 'admin':
        sensores = get_all_sensors()
    else:
        user_id = session.get('user_id')
        sensores = get_user_sensors(user_id)
    
    return render_template("home.html", nome=session.get('user'), sensores=sensores)

@app.route("/end", methods=['GET'])
def display_end_page():
    """Display the end page with running sensors."""
    if session.get('permissao') == 'admin':
        query = "SELECT id FROM sistemas_sensores WHERE status = 'running' ORDER BY id"
        sensores = execute_query(query)
    else:
        user_id = session.get('user_id')
        query = """
        SELECT s.id 
        FROM sistemas_sensores s
        JOIN utilizadores_sensores us ON s.id = us.id_sensor
        WHERE us.id_utilizador = %s AND s.status = 'running'
        ORDER BY s.id
        """
        sensores = execute_query(query, (user_id,))
    
    return render_template("end.html", sensores=sensores or [])

@app.route("/start-generator", methods=['POST'])
def start_generator():
    """Start generator by sending a request to the service manager."""
    generator_id = request.form.get("sensor_id")
    sampling_time = request.form.get("sampling_time")

    if not generator_id or not sampling_time:
        return jsonify({"error": "Generator ID or sampling time not provided!"}), 400
    
    # Check if user has access to this sensor
    if not check_sensor_access(generator_id):
        return jsonify({"error": "Acesso negado a este sensor!"}), 403

    try:
        # Format the message as START|sensor_id|sampling_time
        start_message = f"START|{generator_id}|{sampling_time}"
        
        response = requests.post(
            "http://127.0.0.1:5000/start",
            json={"sensor_id": generator_id, "sampling_time": sampling_time, "message": start_message},
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 200:
            return redirect(url_for('display_home_page'))
        else:
            return jsonify({"error": f"Failed to start generator. {response.text}"}), 500
    except requests.RequestException as e:
        return jsonify({"error": f"Connection error: {str(e)}"}), 500

@app.route("/end-generator", methods=['POST'])
def end_generator():
    """Send end signal to Service Manager."""
    generator_id = request.form.get("generator_id")

    if not generator_id:
        return jsonify({"error": "Generator ID not provided!"}), 400
    
    # Check if user has access to this sensor
    if not check_sensor_access(generator_id):
        return jsonify({"error": "Acesso negado a este sensor!"}), 403

    try:
        # Use the correct URL endpoint for stopping a generator
        response = requests.post(
            "http://127.0.0.1:5000/end",  # Ensure this endpoint is correct in your Service Manager
            json={"generator_id": generator_id},
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 200:
            return redirect(url_for('display_home_page'))
        else:
            return jsonify({"error": f"Failed to end generator. {response.text}"}), 500
    except requests.RequestException as e:
        return jsonify({"error": f"Connection error: {str(e)}"}), 500

@app.route("/consulta", methods=['GET'])
def display_index():
    """Display query page"""
    return render_template("index.html")

@app.route("/eventos", methods=['GET'])
def display_eventos():
    """Display events page with sensor alerts"""
    if session.get('permissao') == 'admin':
        query = """
        SELECT es.id_evento_sensor, es.evento_sensor_timestamp, 
            es.id_sensor, es.tipo_alerta, es.valor_alerta
        FROM evento_sensor es
        ORDER BY es.evento_sensor_timestamp DESC
        LIMIT 100
        """
        eventos = execute_query(query)
    else:
        user_id = session.get('user_id')
        query = """
        SELECT es.id_evento_sensor, es.evento_sensor_timestamp, 
            es.id_sensor, es.tipo_alerta, es.valor_alerta
        FROM evento_sensor es
        JOIN utilizadores_sensores us ON es.id_sensor = us.id_sensor
        WHERE us.id_utilizador = %s
        ORDER BY es.evento_sensor_timestamp DESC
        LIMIT 100
        """
        eventos = execute_query(query, (user_id,))

    # Format timestamps for display
    if eventos:
        for evento in eventos:
            if 'evento_sensor_timestamp' in evento and evento['evento_sensor_timestamp']:
                evento['evento_sensor_timestamp'] = evento['evento_sensor_timestamp'].strftime("%Y-%m-%d %H:%M:%S")

    return render_template("eventos.html", eventos=eventos or [])

@app.route("/eventos-data", methods=['GET'])
def get_eventos_data():
    """Return events data in JSON format for real-time updates"""
    if session.get('permissao') == 'admin':
        query = """
        SELECT es.id_evento_sensor, es.evento_sensor_timestamp, 
            es.id_sensor, es.tipo_alerta, es.valor_alerta
        FROM evento_sensor es
        ORDER BY es.evento_sensor_timestamp DESC
        LIMIT 100
        """
        eventos = execute_query(query)
    else:
        user_id = session.get('user_id')
        query = """
        SELECT es.id_evento_sensor, es.evento_sensor_timestamp, 
            es.id_sensor, es.tipo_alerta, es.valor_alerta
        FROM evento_sensor es
        JOIN utilizadores_sensores us ON es.id_sensor = us.id_sensor
        WHERE us.id_utilizador = %s
        ORDER BY es.evento_sensor_timestamp DESC
        LIMIT 100
        """
        eventos = execute_query(query, (user_id,))

    # Format timestamps for JSON
    if eventos:
        for evento in eventos:
            if 'evento_sensor_timestamp' in evento and evento['evento_sensor_timestamp']:
                evento['evento_sensor_timestamp'] = evento['evento_sensor_timestamp'].strftime("%Y-%m-%d %H:%M:%S")

    return jsonify(eventos or [])
    
@app.route("/erro", methods=['GET'])
def display_error_page():
    """Display the error page with database records"""
    if session.get('permissao') == 'admin':
        query = "SELECT * FROM erros ORDER BY timestamp DESC"
        erros = execute_query(query)
    else:
        user_id = session.get('user_id')
        query = """
        SELECT e.* 
        FROM erros e
        JOIN utilizadores_sensores us ON e.sensor_id = us.id_sensor
        WHERE us.id_utilizador = %s
        ORDER BY e.timestamp DESC
        """
        erros = execute_query(query, (user_id,))

    return render_template("error.html", erros=erros or [])

@app.route("/error-data", methods=['GET'])
def get_error_data():
    """Provide errors in JSON format for automatic updates."""
    if session.get('permissao') == 'admin':
        query = "SELECT * FROM erros ORDER BY timestamp DESC"
        erros = execute_query(query)
    else:
        user_id = session.get('user_id')
        query = """
        SELECT e.*
        FROM erros e
        JOIN utilizadores_sensores us ON e.sensor_id = us.id_sensor
        WHERE us.id_utilizador = %s
        ORDER BY e.timestamp DESC
        """
        erros = execute_query(query, (user_id,))

    # Format timestamps for JSON
    if erros:
        for erro in erros:
            if 'timestamp' in erro and erro['timestamp']:
                erro['timestamp'] = erro['timestamp'].strftime("%Y-%m-%d %H:%M:%S")

    return jsonify(erros or [])

@app.route("/sensor-details", methods=['GET'])
def sensor_details():
    """Page to view detailed sensor data"""
    sensor_id = request.args.get('sensor_id')
    
    # Check if user has access to this sensor
    if not check_sensor_access(sensor_id):
        return "Acesso negado a este sensor!", 403

    return render_template("sensor_details.html", sensor_id=sensor_id)

@app.route("/sensor-data/<sensor_id>", methods=['GET'])
def get_sensor_data(sensor_id):
    """Retrieve detailed data for a specific sensor"""
    # Check if user has access to this sensor
    if not check_sensor_access(sensor_id):
        return jsonify({"error": "Acesso negado"}), 403

    query = """
    SELECT * FROM dados 
    WHERE sensor_id = %s 
    ORDER BY timestamp DESC 
    LIMIT 100
    """
    sensor_data = execute_query(query, (sensor_id,))

    # Convert timestamp to string
    if sensor_data:
        for row in sensor_data:
            row['timestamp'] = row['timestamp'].strftime("%Y-%m-%d %H:%M:%S")

    return jsonify(sensor_data or [])

@app.route("/filtered-results")
def filtered_results():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    selected_sensors = request.args.get('sensors', '').split(',') if request.args.get('sensors') else []
    selected_metrics = request.args.get('metrics', '').split(',') if request.args.get('metrics') else []
    all_dates = request.args.get('all_dates')
    
    # Filter out empty strings
    selected_sensors = [s for s in selected_sensors if s]
    selected_metrics = [m for m in selected_metrics if m]
    
    return render_template('filtered-results.html', 
                           start_date=start_date, 
                           end_date=end_date, 
                           selected_sensors=selected_sensors,
                           selected_metrics=selected_metrics,
                           all_dates=all_dates)

@app.route("/filtered-data", methods=['GET'])
def get_filtered_data():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    all_dates = request.args.get('all_dates', 'false')
    selected_sensors = request.args.get('sensors', '').split(',') if request.args.get('sensors') else []
    selected_metrics = request.args.get('metrics', '').split(',') if request.args.get('metrics') else []
    
    # Filter out empty strings
    selected_sensors = [s for s in selected_sensors if s]
    selected_metrics = [m for m in selected_metrics if m]
    
    # Sensors are required for any query
    if not selected_sensors:
        return jsonify({"error": "At least one sensor must be selected"}), 400
    
    # Either metrics or date range should be provided (or both)
    if not selected_metrics and not (start_date and end_date) and all_dates != 'true':
        return jsonify({"error": "Either select at least one metric or provide a date range"}), 400
    
    try:
        # Define base columns to always select
        base_columns = ["sensor_id", "timestamp"]
        all_metric_columns = ["temperature", "humidity", "CO2", "Lum"]
        
        # If specific metrics were selected, use only those, otherwise use all
        metric_columns = [col for col in all_metric_columns if col in selected_metrics] if selected_metrics else all_metric_columns
        selected_columns = base_columns + metric_columns
        
        # Build the query with selected columns
        query = f"""
        SELECT {', '.join(selected_columns)}
        FROM dados
        WHERE 1=1
        """
        
        query_params = []
        
        # Add date filter if provided
        if start_date and end_date:
            query += " AND timestamp BETWEEN %s AND %s"
            query_params.extend([f"{start_date} 00:00:00", f"{end_date} 23:59:59"])
        
        # Add sensor filter if provided
        if selected_sensors:
            # Convert sensors to integers
            try:
                valid_sensors = [int(sensor) for sensor in selected_sensors]
                if valid_sensors:
                    placeholders = ','.join(['%s'] * len(valid_sensors))
                    query += f" AND sensor_id IN ({placeholders})"
                    query_params.extend(valid_sensors)
            except ValueError:
                return jsonify({"error": "Invalid sensor IDs"}), 400
        
        # Add ORDER BY clause first 
        query += " ORDER BY sensor_id, timestamp DESC"
        
        # Add LIMIT clause if needed
        if not (start_date and end_date) and all_dates == 'true':
            query += " LIMIT 1000"
        
        filtered_data = execute_query(query, query_params)
        
        # Group data by sensor
        grouped_data = {}
        if filtered_data:
            for row in filtered_data:
                sensor_id = str(row['sensor_id'])
                if sensor_id not in grouped_data:
                    grouped_data[sensor_id] = []
                
                # Convert timestamp to string
                row['timestamp'] = row['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
                
                # Only include requested metrics in the response
                row_data = {'timestamp': row['timestamp']}
                for metric in selected_metrics if selected_metrics else all_metric_columns:
                    if metric in row:
                        row_data[metric] = row[metric]
                
                grouped_data[sensor_id].append(row_data)
        
        # Convert to list of dictionaries in the format expected by the frontend
        result = [
            {
                "sensor_id": sensor_id,
                "values": values
            } 
            for sensor_id, values in grouped_data.items()
        ]
        
        return jsonify(result)
    
    except Exception as e:
        print(f"Error fetching filtered data: {e}")
        return jsonify({"error": f"Error retrieving data: {str(e)}"}), 500

@app.route("/data", methods=['GET'])
def get_data():
    """Return last 5 values for each sensor"""
    combined_data = []
    
    # If admin, show all data
    if session.get('permissao') == 'admin':
        for sensor_id, values in data_by_generator.items():
            sorted_values = sorted(values, key=lambda x: x['timestamp'], reverse=True)
            combined_data.append({
                "sensor_id": sensor_id,
                "values": list(sorted_values)
            })
    else:
        # For regular users, only show data for their sensors
        user_id = session.get('user_id')
        user_sensors = get_user_sensors(user_id)
        user_sensor_ids = [sensor['id'] for sensor in user_sensors]
        
        for sensor_id, values in data_by_generator.items():
            if int(sensor_id) in user_sensor_ids:
                sorted_values = sorted(values, key=lambda x: x['timestamp'], reverse=True)
                combined_data.append({
                    "sensor_id": sensor_id,
                    "values": list(sorted_values)
                })
    
    return jsonify(combined_data)

@app.route('/notificacao')
def notificacao():
    return render_template('not.html')

@app.route('/save_telegram_config', methods=['POST'])
def save_telegram_config():
    if 'user_id' not in session:
        return "Sessão expirada. Faça login novamente.", 401
    
    user_id = session.get('user_id')
    
    if not request.json:
        return "Dados inválidos", 400
    
    token = request.json.get('token')
    chat_id = request.json.get('chatId')
    
    if not token or not chat_id:
        return "Token ou Chat ID não fornecidos", 400
    
    try:
        response = requests.post(
            "http://127.0.0.1:5000/update_telegram_config",
            json={
                "token": token,
                "chat_id": chat_id,
                "user_id": user_id 
            },
            headers={'Content-Type': 'application/json'}
        )
        
        if response.status_code == 200:
            return jsonify({"message": "Configurações do Telegram salvas com sucesso!"}), 200
        else:
            return f"Erro ao salvar configurações: {response.text}", 500
    
    except requests.RequestException as e:
        return f"Erro de conexão: {str(e)}", 500

def stop_monitoring():
    """Function to properly stop the monitoring thread"""
    global monitoring_active
    monitoring_active = False

@app.route('/remover-utilizador', methods=['POST'])
@login_required('admin')
def remover_utilizador():
    nome_utilizador = request.form.get('nome_utilizador')

    if not nome_utilizador:
        return render_template('admin_dashboard.html', mensagem="Nome do utilizador não fornecido.", status="error")

    try:
        response = requests.post(
            "http://127.0.0.1:5000/delete_user",
            json={"username": nome_utilizador},
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 200:
            return render_template('admin_dashboard.html', mensagem=f"Utilizador '{nome_utilizador}' removido com sucesso.", status="success")
        else:
            error_data = response.json()
            return render_template('admin_dashboard.html', mensagem=f"Erro ao remover utilizador: {error_data.get('error')}", status="error")
    except requests.RequestException as e:
        return render_template('admin_dashboard.html', mensagem=f"Erro de conexão: {str(e)}", status="error")

@app.route('/remover_sensor', methods=['POST'])
@login_required('admin')
def remover_sensor():
    sensor_id = request.form.get('sensor_id')
    if not sensor_id:
        return "ID do sensor não fornecido.", 400

    try:
        response = requests.post(
            "http://127.0.0.1:5000/delete_sensor",
            json={"sensor_id": sensor_id},
            headers={'Content-Type': 'application/json'}
        )

        if response.status_code == 200:
            return redirect(url_for('pagina_sensores'))
        else:
            return f"Erro ao remover sensor: {response.text}", 500
    except requests.RequestException as e:
        return f"Erro de conexão: {str(e)}", 500

@app.route('/area', methods=['GET', 'POST'])
@login_required()
def area():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    

    query = "SELECT image_path FROM utilizador_imagem WHERE user_id = %s"
    result = execute_query(query, (user_id,), fetch_all=False)
    
    image_path = result['image_path'] if result else None

    areas = []
    if image_path:
        query = """
                SELECT a.id, a.x, a.y, a.sensor_x, a.sensor_y, a.width, a.height, a.description, a.sensor_id, 
                    CASE
                        WHEN a.sensor_id IS NULL THEN 'Sem sensor'
                        ELSE IFNULL(s.status, 'Desconhecido')
                    END as sensor_status
                FROM areas_utilizador a
                LEFT JOIN sistemas_sensores s ON a.sensor_id = s.id
                WHERE a.user_id = %s 
                ORDER BY a.id
        """
        areas_result = execute_query(query, (user_id,))
        areas = areas_result if areas_result else []
    
    # Procurar sensores disponíveis
    try:
        response = requests.get(
            "http://127.0.0.1:5000/get_available_sensors",
            params={"user_id": user_id}
        )
        if response.status_code == 200:
            available_sensors = response.json().get('sensors', [])
        else:
            available_sensors = []
    except requests.RequestException:
        available_sensors = []
    
    
    if request.method == 'POST':
        # Verificar se é um upload de imagem
        if 'image' in request.files:
            file = request.files['image']
            
            # Verificar se o arquivo tem nome
            if file.filename == '':
                return "Nenhum arquivo selecionado", 400
            
            # Verificar se é uma imagem válida
            if file and allowed_file(file.filename):
                # Criar diretório de uploads se não existir
                upload_folder = os.path.join(app.root_path, 'static', 'uploads')
                os.makedirs(upload_folder, exist_ok=True)
                
                # Guardar com nome único baseado no user_id
                filename = f"user_{user_id}_{int(time.time())}{os.path.splitext(file.filename)[1]}"
                file_path = os.path.join(upload_folder, filename)
                file.save(file_path)
                
                # Caminho relativo para uso no HTML
                rel_path = f"/static/uploads/{filename}"
                
                # Enviar para o gestor de serviço fazer a atualização na base de dados
                try:
                    response = requests.post(
                        "http://127.0.0.1:5000/update_user_image",
                        json={"user_id": user_id, "image_path": rel_path},
                        headers={'Content-Type': 'application/json'}
                    )
                    
                    if response.status_code != 200:
                        return f"Erro ao atualizar imagem: {response.text}", 500
                        
                    # Atualizar a variável para exibição imediata
                    image_path = rel_path
                    # Limpar as áreas quando uma nova imagem é carregada
                    response = requests.post(
                        "http://127.0.0.1:5000/clear_user_areas",
                        json={"user_id": user_id},
                        headers={'Content-Type': 'application/json'}
                    )
                    areas = []
                    
                except requests.RequestException as e:
                    return f"Erro de conexão: {str(e)}", 500
        
        # Verificar se é adição de uma área
        elif 'save_area' in request.form:
            x = request.form.get('x')
            y = request.form.get('y')
            width = request.form.get('width')
            height = request.form.get('height')
            description = request.form.get('description', '')
            sensor_id = request.form.get('sensor_id')
            sensor_x = request.form.get('sensor_x')
            sensor_y = request.form.get('sensor_y')
            
            # Se não for selecionado um sensor, definir como NULL
            if sensor_id == '' or sensor_id is None:
                sensor_id = None
                sensor_x = None
                sensor_y = None
            else:
                # Ensure sensor_id is treated as an integer
                sensor_id = int(sensor_id)
            
            try:
                # Criar nova área
                response = requests.post(
                    "http://127.0.0.1:5000/save_user_area",
                    json={
                        "user_id": user_id,
                        "x": float(x),
                        "y": float(y),
                        "width": float(width),
                        "height": float(height),
                        "description": description,
                        "sensor_id": sensor_id,
                        "sensor_x": float(sensor_x) if sensor_x else None,
                        "sensor_y": float(sensor_y) if sensor_y else None
                    },
                    headers={'Content-Type': 'application/json'}
                )
                
                if response.status_code != 200:
                    return f"Erro ao salvar área: {response.text}", 500
                    
                # Atualizar a lista de áreas
                query = """
                    SELECT a.id, a.x, a.y, a.sensor_x, a.sensor_y, a.width, a.height, a.description, a.sensor_id, 
                          CASE
                              WHEN a.sensor_id IS NULL THEN 'Sem sensor'
                              ELSE IFNULL(s.status, 'Desconhecido')
                          END as sensor_status
                    FROM areas_utilizador a
                    LEFT JOIN sistemas_sensores s ON a.sensor_id = s.id
                    WHERE a.user_id = %s 
                    ORDER BY a.id
                """
                areas_result = execute_query(query, (user_id,))
                areas = areas_result if areas_result else []
            except requests.RequestException as e:
                return f"Erro de conexão: {str(e)}", 500        
        
        # Verificar se é exclusão de área
        elif 'delete_area' in request.form:
            area_id = request.form.get('area_id')
            
            try:
                response = requests.post(
                    "http://127.0.0.1:5000/delete_user_area",
                    json={"area_id": area_id, "user_id": user_id},
                    headers={'Content-Type': 'application/json'}
                )
                
                if response.status_code != 200:
                    return f"Erro ao excluir área: {response.text}", 500
                    
                # Atualizar a lista de áreas
                query = """
                    SELECT a.id, a.x, a.y, a.sensor_x, a.sensor_y, a.width, a.height, a.description, a.sensor_id, 
                          CASE
                              WHEN a.sensor_id IS NULL THEN 'Sem sensor'
                              ELSE IFNULL(s.status, 'Desconhecido')
                          END as sensor_status
                    FROM areas_utilizador a
                    LEFT JOIN sistemas_sensores s ON a.sensor_id = s.id
                    WHERE a.user_id = %s 
                    ORDER BY a.id
                """
                areas_result = execute_query(query, (user_id,))
                areas = areas_result if areas_result else []
                
            except requests.RequestException as e:
                return f"Erro de conexão: {str(e)}", 500
    
    return render_template('area.html', 
                                    image_path=image_path, 
                                    user_name=session.get('user'), 
                                    areas=areas,
                                    available_sensors=available_sensors)

@app.route('/area_sensor_data/<int:area_id>')
@login_required()
def area_sensor_data(area_id):
    """Exibe os dados do sensor associado a uma área específica"""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    
    # Verificar se a área pertence ao utilizadors
    query = """
        SELECT a.id, a.description, a.sensor_id, s.status
        FROM areas_utilizador a
        LEFT JOIN sistemas_sensores s ON a.sensor_id = s.id
        WHERE a.id = %s AND a.user_id = %s
    """
    area_info = execute_query(query, (area_id, user_id), fetch_all=False)
    
    if not area_info:
        return redirect(url_for('area'))
    
    if not area_info['sensor_id']:
        return redirect(url_for('area'))
    
    # Procurar os dados mais recentes do sensor
    query = """
        SELECT timestamp, temperature, humidity, CO2, Lum
        FROM dados
        WHERE sensor_id = %s
        ORDER BY timestamp DESC
        LIMIT 100
    """
    sensor_data = execute_query(query, (area_info['sensor_id'],))
    
    # Procurar alertas recentes para este sensor
    query = """
        SELECT evento_sensor_timestamp, tipo_alerta, valor_alerta
        FROM evento_sensor
        WHERE id_sensor = %s
        ORDER BY evento_sensor_timestamp DESC
        LIMIT 20
    """
    alerts = execute_query(query, (area_info['sensor_id'],))
    
    return render_template('area_sensor_data.html', 
                          area=area_info, 
                          sensor_data=sensor_data, 
                          alerts=alerts)

@app.route('/area_heatmap/risk/<int:area_id>')
@login_required()
def area_risk_heatmap(area_id):
    """Exibe o mapa de calor de risco para uma área específica"""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
   
    # Verificar se a área pertence ao utilizador
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height,
               a.sensor_x, a.sensor_y, s.status, i.image_path
        FROM areas_utilizador a
        LEFT JOIN sistemas_sensores s ON a.sensor_id = s.id
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE a.id = %s AND a.user_id = %s
    """
    area_info = execute_query(query, (area_id, user_id), fetch_all=False)
   
    if not area_info:
        return redirect(url_for('area'))
        
    # Procurar todas as áreas com sensores nesta imagem
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height,
               a.sensor_x, a.sensor_y
        FROM areas_utilizador a
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE i.image_path = %s AND a.user_id = %s AND a.sensor_id IS NOT NULL
    """
    areas_with_sensors = execute_query(query, (area_info['image_path'], user_id))
    
    if not areas_with_sensors:
        return redirect(url_for('area'))
    
    # Definir constantes para risco
    MEDIUM_RISK_THRESHOLD = 35
    HIGH_RISK_THRESHOLD = 60
    
    # Preparar dados de sensores para o mapa de calor
    sensors_data = []
    for sensor_area in areas_with_sensors:
        # Procurar APENAS O ÚLTIMO REGISTRO do sensor
        query = """
            SELECT timestamp, temperature, humidity, CO2, Lum
            FROM dados
            WHERE sensor_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """
        sensor_data = execute_query(query, (sensor_area['sensor_id'],), fetch_all=False)
        
        if not sensor_data:
            continue
            
        # Calcular o risco com o ÚLTIMO VALOR registado
        risk_value = calculate_risk(
            sensor_data['temperature'],
            sensor_data['humidity'],
            sensor_data['CO2'],
            sensor_data['Lum']
        )
        
        # Usar sensor_x e sensor_y se disponíveis, caso contrário, usar o centro da área
        sensor_x = sensor_area['sensor_x'] if sensor_area['sensor_x'] is not None else (sensor_area['x'] + sensor_area['width']/2)
        sensor_y = sensor_area['sensor_y'] if sensor_area['sensor_y'] is not None else (sensor_area['y'] + sensor_area['height']/2)
        
        sensors_data.append({
            'id': sensor_area['id'],
            'x': sensor_x,
            'y': sensor_y,
            'width': sensor_area['width'],
            'height': sensor_area['height'],
            'temperature': sensor_data['temperature'],
            'humidity': sensor_data['humidity'],
            'co2': sensor_data['CO2'],
            'lum': sensor_data['Lum'],
            'risk_value': risk_value,
            'last_reading': sensor_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
        })
    
    # Thresholds para risco
    thresholds = {
        'medium': MEDIUM_RISK_THRESHOLD,
        'high': HIGH_RISK_THRESHOLD
    }
   
    return render_template('risk_heatmap.html',
                          area=area_info,
                          sensors=sensors_data,
                          medium_threshold=thresholds['medium'],
                          high_threshold=thresholds['high'],
                          type='risk')

@app.route('/api/sensor_data/<int:area_id>')
@login_required()
def api_sensor_data(area_id):
    """Retorna o último valor registrado dos sensores para uma área específica"""
    import datetime
    import sys
    
    # Log para debug
    print(f"[{datetime.datetime.now()}] Requisição recebida para /api/sensor_data/{area_id}", file=sys.stderr)
    
    user_id = session.get('user_id')
    if not user_id:
        print(f"[{datetime.datetime.now()}] Usuário não autenticado", file=sys.stderr)
        return jsonify({'error': 'Não autenticado'}), 401
    
    print(f"[{datetime.datetime.now()}] Usuário autenticado: {user_id}", file=sys.stderr)
    
    # Verificar se a área pertence ao utilizador e obter o caminho da imagem
    query = """
        SELECT i.image_path
        FROM areas_utilizador a
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE a.id = %s AND a.user_id = %s
    """
    area_info = execute_query(query, (area_id, user_id), fetch_all=False)
    
    if not area_info:
        print(f"[{datetime.datetime.now()}] Área não encontrada: {area_id}", file=sys.stderr)
        return jsonify({'error': 'Área não encontrada'}), 404
    
    print(f"[{datetime.datetime.now()}] Área encontrada, image_path: {area_info['image_path']}", file=sys.stderr)
    
    # Procurar todas as áreas com sensores nesta imagem
    query = """
        SELECT a.id, a.sensor_id, a.x, a.y, a.width, a.height,
               a.sensor_x, a.sensor_y, s.status
        FROM areas_utilizador a
        LEFT JOIN sistemas_sensores s ON a.sensor_id = s.id
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE i.image_path = %s AND a.user_id = %s AND a.sensor_id IS NOT NULL
    """
    areas_with_sensors = execute_query(query, (area_info['image_path'], user_id))
    
    if not areas_with_sensors:
        print(f"[{datetime.datetime.now()}] Nenhum sensor encontrado para a área", file=sys.stderr)
        return jsonify({'error': 'Nenhum sensor encontrado'}), 404
    
    print(f"[{datetime.datetime.now()}] Encontradas {len(areas_with_sensors)} áreas com sensores", file=sys.stderr)
    
    # Preparar dados de sensores
    sensors_data = []
    for sensor_area in areas_with_sensors:
        print(f"[{datetime.datetime.now()}] Processando sensor ID: {sensor_area['sensor_id']}", file=sys.stderr)
        
        # Procurar APENAS O ÚLTIMO REGISTRO do sensor
        query = """
            SELECT temperature, humidity, CO2, Lum, timestamp
            FROM dados
            WHERE sensor_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """
        last_reading = execute_query(query, (sensor_area['sensor_id'],), fetch_all=False)
        
        if not last_reading:
            print(f"[{datetime.datetime.now()}] Sem leituras para o sensor ID: {sensor_area['sensor_id']}", file=sys.stderr)
            continue 
        
        # Log para debug
        print(f"[{datetime.datetime.now()}] Última leitura para sensor ID {sensor_area['sensor_id']}: {last_reading}", file=sys.stderr)
        
        # Calcular o risco com o ÚLTIMO VALOR registado
        risk_value = (last_reading['temperature'] * 0.55) - \
                     (last_reading['humidity'] * 0.35) + \
                     (last_reading['CO2'] * 0.05) + \
                     (last_reading['Lum'] * 0.05)
        
        # Garantir que o risco não seja negativo
        risk_value = max(0, min(100, risk_value))  # Limitar entre 0 e 100
        
        # Usar sensor_x e sensor_y se disponíveis, caso contrário, usar o centro da área
        sensor_x = sensor_area['sensor_x'] if sensor_area['sensor_x'] is not None else (sensor_area['x'] + sensor_area['width']/2)
        sensor_y = sensor_area['sensor_y'] if sensor_area['sensor_y'] is not None else (sensor_area['y'] + sensor_area['height']/2)
        
        sensors_data.append({
            'id': sensor_area['id'],
            'sensor_id': sensor_area['sensor_id'],
            'x': float(sensor_x),
            'y': float(sensor_y),
            'width': float(sensor_area['width']),
            'height': float(sensor_area['height']),
            'temperature': float(last_reading['temperature']),
            'humidity': float(last_reading['humidity']),
            'co2': float(last_reading['CO2']),
            'lum': float(last_reading['Lum']),
            'risk_value': float(risk_value),
            'last_reading': last_reading['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
        })
    
    print(f"[{datetime.datetime.now()}] Retornando {len(sensors_data)} sensores com dados", file=sys.stderr)
    
    
    if sensors_data:
        print(f"[{datetime.datetime.now()}] Exemplo de dados (primeiro sensor): {sensors_data[0]}", file=sys.stderr)
    
    # Adicionar cabeçalho para evitar cache
    response = jsonify(sensors_data)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@app.route('/area_heatmap/co2/<int:area_id>')
@login_required()
def area_co2_heatmap(area_id):
    """Exibe o mapa de calor de CO2 para uma área específica"""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
   
    # Verificar se a área pertence ao utilizador
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height,
               a.sensor_x, a.sensor_y, s.status, i.image_path
        FROM areas_utilizador a
        LEFT JOIN sistemas_sensores s ON a.sensor_id = s.id
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE a.id = %s AND a.user_id = %s
    """
    area_info = execute_query(query, (area_id, user_id), fetch_all=False)
   
    if not area_info:
        return redirect(url_for('area'))
        
    # Procurar todas as áreas com sensores nesta imagem
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height,
               a.sensor_x, a.sensor_y
        FROM areas_utilizador a
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE i.image_path = %s AND a.user_id = %s AND a.sensor_id IS NOT NULL
    """
    areas_with_sensors = execute_query(query, (area_info['image_path'], user_id))
    
    if not areas_with_sensors:
        return redirect(url_for('area'))
    
    # Definir constantes para CO2
    MEDIUM_CO2_THRESHOLD = 550
    HIGH_CO2_THRESHOLD = 1000
    
    # Preparar dados de sensores para o mapa de calor
    sensors_data = []
    for sensor_area in areas_with_sensors:
        # Buscar o dado mais recente do sensor
        query = """
            SELECT timestamp, temperature, humidity, CO2, Lum
            FROM dados
            WHERE sensor_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """
        sensor_data = execute_query(query, (sensor_area['sensor_id'],), fetch_all=False)
        
        if not sensor_data:
            continue
            
        # Usar sensor_x e sensor_y se disponíveis, caso contrário, usar o centro da área
        sensor_x = sensor_area['sensor_x'] if sensor_area['sensor_x'] is not None else (sensor_area['x'] + sensor_area['width']/2)
        sensor_y = sensor_area['sensor_y'] if sensor_area['sensor_y'] is not None else (sensor_area['y'] + sensor_area['height']/2)
        
        sensors_data.append({
            'id': sensor_area['id'],
            'x': sensor_x,
            'y': sensor_y,
            'width': sensor_area['width'],
            'height': sensor_area['height'],
            'co2': sensor_data['CO2'],
            'temperature': sensor_data['temperature'],
            'humidity': sensor_data['humidity'],
            'lum': sensor_data['Lum']
        })
    
    # Thresholds para CO2
    thresholds = {
        'medium': MEDIUM_CO2_THRESHOLD,
        'high': HIGH_CO2_THRESHOLD
    }
   
    return render_template('co2_heatmap.html',
                          area=area_info,
                          sensors=sensors_data,
                          medium_threshold=thresholds['medium'],
                          high_threshold=thresholds['high'])

@app.route('/api/sensor_co2_data/<int:area_id>')
@login_required()
def api_get_sensor_co2_data(area_id):
    """API endpoint para obter dados de CO2 dos sensores para uma área específica"""
    import datetime
    import sys
    
    print(f"[{datetime.datetime.now()}] Requisição recebida para /api/sensor_co2_data/{area_id}", file=sys.stderr)
    
    user_id = session.get('user_id')
    if not user_id:
        print(f"[{datetime.datetime.now()}] Usuário não autenticado", file=sys.stderr)
        return jsonify({'error': 'Não autenticado'}), 401
    
    print(f"[{datetime.datetime.now()}] Usuário autenticado: {user_id}", file=sys.stderr)
    
    # Verificar se a área pertence ao utilizador
    query = """
        SELECT a.id, a.description, i.image_path
        FROM areas_utilizador a
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE a.id = %s AND a.user_id = %s
    """
    area_info = execute_query(query, (area_id, user_id), fetch_all=False)
    
    if not area_info:
        print(f"[{datetime.datetime.now()}] Área não encontrada: {area_id}", file=sys.stderr)
        return jsonify({'error': 'Área não encontrada'}), 404
    
    print(f"[{datetime.datetime.now()}] Área encontrada, image_path: {area_info['image_path']}", file=sys.stderr)
    
    # Procurar todas as áreas com sensores nesta imagem
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height,
               a.sensor_x, a.sensor_y
        FROM areas_utilizador a
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE i.image_path = %s AND a.user_id = %s AND a.sensor_id IS NOT NULL
    """
    areas_with_sensors = execute_query(query, (area_info['image_path'], user_id))
    
    if not areas_with_sensors:
        print(f"[{datetime.datetime.now()}] Nenhum sensor encontrado para a área", file=sys.stderr)
        return jsonify({'error': 'Não foram encontrados sensores para esta área'}), 404
    
    print(f"[{datetime.datetime.now()}] Encontradas {len(areas_with_sensors)} áreas com sensores", file=sys.stderr)
    
    # Preparar dados de sensores para o mapa de calor
    sensors_data = []
    for sensor_area in areas_with_sensors:
        print(f"[{datetime.datetime.now()}] Processando sensor ID: {sensor_area['sensor_id']}", file=sys.stderr)
        
        # Buscar o dado mais recente do sensor
        query = """
            SELECT timestamp, temperature, humidity, CO2, Lum
            FROM dados
            WHERE sensor_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """
        sensor_data = execute_query(query, (sensor_area['sensor_id'],), fetch_all=False)
        
        if not sensor_data:
            print(f"[{datetime.datetime.now()}] Sem leituras para o sensor ID: {sensor_area['sensor_id']}", file=sys.stderr)
            continue
            
        
        print(f"[{datetime.datetime.now()}] Última leitura para sensor ID {sensor_area['sensor_id']}: {sensor_data}", file=sys.stderr)
            
        # Usar sensor_x e sensor_y se disponíveis, caso contrário, usar o centro da área
        sensor_x = sensor_area['sensor_x'] if sensor_area['sensor_x'] is not None else (sensor_area['x'] + sensor_area['width']/2)
        sensor_y = sensor_area['sensor_y'] if sensor_area['sensor_y'] is not None else (sensor_area['y'] + sensor_area['height']/2)
        
        sensors_data.append({
            'id': sensor_area['id'],
            'sensor_id': sensor_area['sensor_id'],
            'x': float(sensor_x),
            'y': float(sensor_y),
            'width': float(sensor_area['width']),
            'height': float(sensor_area['height']),
            'co2': float(sensor_data['CO2']),
            'temperature': float(sensor_data['temperature']),
            'humidity': float(sensor_data['humidity']),
            'lum': float(sensor_data['Lum']),
            'last_reading': sensor_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
        })
    
    print(f"[{datetime.datetime.now()}] Retornando {len(sensors_data)} sensores com dados", file=sys.stderr)
    
    if sensors_data:
        print(f"[{datetime.datetime.now()}] Exemplo de dados (primeiro sensor): {sensors_data[0]}", file=sys.stderr)
    
    # Adicionar cabeçalho para evitar cache
    response = jsonify(sensors_data)
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@app.route('/area_heatmap/humidity/<int:area_id>')
@login_required()
def area_humidity_heatmap(area_id):
    """Exibe o mapa de calor de humidade para uma área específica"""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    
    # Verificar se a área pertence ao utilizador
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height, a.sensor_x, a.sensor_y, s.status, i.image_path 
        FROM areas_utilizador a 
        LEFT JOIN sistemas_sensores s ON a.sensor_id = s.id 
        JOIN utilizador_imagem i ON i.user_id = a.user_id 
        WHERE a.id = %s AND a.user_id = %s
    """
    area_info = execute_query(query, (area_id, user_id), fetch_all=False)
    
    if not area_info:
        return redirect(url_for('area'))
    
    # Procurar todas as áreas com sensores nesta imagem
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height, a.sensor_x, a.sensor_y 
        FROM areas_utilizador a 
        JOIN utilizador_imagem i ON i.user_id = a.user_id 
        WHERE i.image_path = %s AND a.user_id = %s AND a.sensor_id IS NOT NULL
    """
    areas_with_sensors = execute_query(query, (area_info['image_path'], user_id))
    
    if not areas_with_sensors:
        return redirect(url_for('area'))
    
    # Definir constantes para humidade
    LOW_HUM_THRESHOLD = 30 
    MEDIUM_HUM_THRESHOLD = 40
    
    # Preparar dados de sensores para o mapa de calor
    sensors_data = []
    for sensor_area in areas_with_sensors:
        # Buscar o dado mais recente do sensor
        query = """
            SELECT timestamp, temperature, humidity, CO2, Lum 
            FROM dados 
            WHERE sensor_id = %s 
            ORDER BY timestamp DESC 
            LIMIT 1
        """
        sensor_data = execute_query(query, (sensor_area['sensor_id'],), fetch_all=False)
        
        if not sensor_data:
            continue
        
        # Usar sensor_x e sensor_y se disponíveis, caso contrário, usar o centro da área
        sensor_x = sensor_area['sensor_x'] if sensor_area['sensor_x'] is not None else (sensor_area['x'] + sensor_area['width']/2)
        sensor_y = sensor_area['sensor_y'] if sensor_area['sensor_y'] is not None else (sensor_area['y'] + sensor_area['height']/2)
        
        sensors_data.append({
            'id': sensor_area['id'],
            'x': sensor_x,
            'y': sensor_y,
            'width': sensor_area['width'],
            'height': sensor_area['height'],
            'humidity': sensor_data['humidity'],
            'temperature': sensor_data['temperature'],
            'timestamp': sensor_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(sensor_data['timestamp'], datetime) else str(sensor_data['timestamp'])
        })
    
    # Thresholds para humidade
    thresholds = {
        'low': LOW_HUM_THRESHOLD,
        'medium': MEDIUM_HUM_THRESHOLD
    }
    
    return render_template('humidity_heatmap.html', area=area_info, sensors=sensors_data, 
                          low_threshold=thresholds['low'], medium_threshold=thresholds['medium'])

@app.route('/api/humidity_data/<int:area_id>')
@login_required()
def get_humidity_data(area_id):
    """API endpoint para obter dados atualizados de humidade para o mapa de calor"""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Não autorizado'}), 401
    
    # Verificar se a área pertence ao utilizador
    query = """
        SELECT a.id, a.description, i.image_path 
        FROM areas_utilizador a 
        JOIN utilizador_imagem i ON i.user_id = a.user_id 
        WHERE a.id = %s AND a.user_id = %s
    """
    area_info = execute_query(query, (area_id, user_id), fetch_all=False)
    
    if not area_info:
        return jsonify({'error': 'Área não encontrada'}), 404
    
    # Procurar todas as áreas com sensores nesta imagem
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height, a.sensor_x, a.sensor_y 
        FROM areas_utilizador a 
        JOIN utilizador_imagem i ON i.user_id = a.user_id 
        WHERE i.image_path = %s AND a.user_id = %s AND a.sensor_id IS NOT NULL
    """
    areas_with_sensors = execute_query(query, (area_info['image_path'], user_id))
    
    if not areas_with_sensors:
        return jsonify({'error': 'Nenhum sensor encontrado'}), 404
    
    sensors_data = []
    for sensor_area in areas_with_sensors:
        # Procurar os dados mais recentes do sensor
        query = """
            SELECT timestamp, temperature, humidity, CO2, Lum 
            FROM dados 
            WHERE sensor_id = %s 
            ORDER BY timestamp DESC 
            LIMIT 1
        """
        sensor_data = execute_query(query, (sensor_area['sensor_id'],), fetch_all=False)
        
        if not sensor_data:
            continue
        
        # Usar sensor_x e sensor_y se disponíveis, caso contrário, usar o centro da área
        sensor_x = sensor_area['sensor_x'] if sensor_area['sensor_x'] is not None else (sensor_area['x'] + sensor_area['width']/2)
        sensor_y = sensor_area['sensor_y'] if sensor_area['sensor_y'] is not None else (sensor_area['y'] + sensor_area['height']/2)
        
        sensors_data.append({
            'id': sensor_area['id'],
            'x': sensor_x,
            'y': sensor_y,
            'width': sensor_area['width'],
            'height': sensor_area['height'],
            'humidity': sensor_data['humidity'],
            'temperature': sensor_data['temperature'],
            'co2': sensor_data['CO2'],
            'lum': sensor_data['Lum'],
            'timestamp': sensor_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(sensor_data['timestamp'], datetime) else str(sensor_data['timestamp'])
        })
    
    return jsonify(sensors_data)

@app.route('/area_heatmap/temperature/<int:area_id>')
@login_required()
def area_temperature_heatmap(area_id):
    """Exibe o mapa de calor de temperatura para uma área específica"""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
   
    # Verificar se a área pertence ao utilizador
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height,
               a.sensor_x, a.sensor_y, s.status, i.image_path
        FROM areas_utilizador a
        LEFT JOIN sistemas_sensores s ON a.sensor_id = s.id
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE a.id = %s AND a.user_id = %s
    """
    area_info = execute_query(query, (area_id, user_id), fetch_all=False)
   
    if not area_info:
        return redirect(url_for('area'))
        
    # Procurar todas as áreas com sensores nesta imagem
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height,
               a.sensor_x, a.sensor_y
        FROM areas_utilizador a
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE i.image_path = %s AND a.user_id = %s AND a.sensor_id IS NOT NULL
    """
    areas_with_sensors = execute_query(query, (area_info['image_path'], user_id))
    
    if not areas_with_sensors:
        return redirect(url_for('area'))
    
    # Definir constantes para temperatura
    MEDIUM_TEMP_THRESHOLD = 35
    HIGH_TEMP_THRESHOLD = 45
    
    # Preparar dados de sensores para o mapa de calor
    sensors_data = []
    for sensor_area in areas_with_sensors:
       
        query = """
            SELECT timestamp, temperature, humidity, CO2, Lum
            FROM dados
            WHERE sensor_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """
        sensor_data = execute_query(query, (sensor_area['sensor_id'],), fetch_all=False)
        
        if not sensor_data:
            continue
            
        # Usar sensor_x e sensor_y se disponíveis, caso contrário, usar o centro da área
        sensor_x = sensor_area['sensor_x'] if sensor_area['sensor_x'] is not None else (sensor_area['x'] + sensor_area['width']/2)
        sensor_y = sensor_area['sensor_y'] if sensor_area['sensor_y'] is not None else (sensor_area['y'] + sensor_area['height']/2)
        
        sensors_data.append({
            'id': sensor_area['id'],
            'x': sensor_x,
            'y': sensor_y,
            'width': sensor_area['width'],
            'height': sensor_area['height'],
            'temperature': sensor_data['temperature'],
            'timestamp': sensor_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(sensor_data['timestamp'], datetime) else str(sensor_data['timestamp'])
        })
    
    # Thresholds para temperatura
    thresholds = {
        'medium': MEDIUM_TEMP_THRESHOLD,
        'high': HIGH_TEMP_THRESHOLD
    }
   
    return render_template('temperature_heatmap.html',
                          area=area_info,
                          sensors=sensors_data,
                          medium_threshold=thresholds['medium'],
                          high_threshold=thresholds['high'])

@app.route('/api/temperature_data/<int:area_id>')
@login_required()
def get_data_sensor(area_id):
    """API para obter dados de sensores em tempo real para o mapa de calor"""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Não autenticado"}), 401
    
    query = """
        SELECT a.id, i.image_path
        FROM areas_utilizador a
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE a.id = %s AND a.user_id = %s
    """
    area_info = execute_query(query, (area_id, user_id), fetch_all=False)
    
    if not area_info:
        return jsonify({"error": "Área não encontrada"}), 404
    
    query = """
        SELECT a.id, a.description, a.sensor_id, a.x, a.y, a.width, a.height,
               a.sensor_x, a.sensor_y
        FROM areas_utilizador a
        JOIN utilizador_imagem i ON i.user_id = a.user_id
        WHERE i.image_path = %s AND a.user_id = %s AND a.sensor_id IS NOT NULL
    """
    areas_with_sensors = execute_query(query, (area_info['image_path'], user_id))
    
    if not areas_with_sensors:
        return jsonify([])
    
    # Preparar dados de sensores para o mapa de calor
    sensors_data = []
    for sensor_area in areas_with_sensors:
        # Procurar o dado mais recente do sensor
        
        query = """
            SELECT temperature, humidity, CO2 as co2, Lum as lum
            FROM dados
            WHERE sensor_id = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """
        sensor_data = execute_query(query, (sensor_area['sensor_id'],), fetch_all=False)
        
        if not sensor_data:
            continue
        
        # Usar sensor_x e sensor_y se disponíveis, caso contrário, usar o centro da área
        sensor_x = sensor_area['sensor_x'] if sensor_area['sensor_x'] is not None else (sensor_area['x'] + sensor_area['width']/2)
        sensor_y = sensor_area['sensor_y'] if sensor_area['sensor_y'] is not None else (sensor_area['y'] + sensor_area['height']/2)
        
        # Combinar os dados do sensor com a posição
        sensor_info = {
            'id': sensor_area['id'],
            'x': sensor_x,
            'y': sensor_y,
            'width': sensor_area['width'],
            'height': sensor_area['height'],
        }
        # Adicionar os dados do sensor
        sensor_info.update(sensor_data)
        
        sensors_data.append(sensor_info)
    
    
    response = jsonify(sensors_data)
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

def calculate_risk(temperature, humidity, co2, lum):
    """Calculate risk based on sensor values"""
    return (temperature * 0.55) - (humidity * 0.35) + (co2 * 0.05) + (lum * 0.05)


if __name__ == "__main__":
    # Load last values from database on startup
    load_last_five_from_database()
    
    # Start database monitoring in separate thread
    monitoring_active = True
    monitor_thread = threading.Thread(target=monitor_database, daemon=True)
    monitor_thread.start()

    try:
        # Configure server to run on HTTP
        app.run(
            host="0.0.0.0",
            port=5001,
            debug=True,
            ssl_context=None  # Explicitly disable SSL/HTTPS
        )
    finally:
        # Make sure to stop the monitoring thread when the application exits
        stop_monitoring()
        if monitor_thread.is_alive():
            monitor_thread.join(timeout=2)  # Give it 2 seconds to finish