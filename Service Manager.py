from flask import Flask, request, jsonify
import os
from datetime import datetime
import mysql.connector
import threading
import paho.mqtt.client as mqtt
import sys
import requests
from mysql.connector import Error
from subprocess import Popen

app = Flask(__name__)

# Database configuration
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Paulinha02',
    'database': 'sistema_dados'
}

# MQTT Configuration
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
# Define topic patterns for specific sensors
MQTT_TOPIC_DATA_PATTERN = "sensor/+/data"  # The + is a wildcard that matches any single topic level
MQTT_TOPIC_CONTROL_PATTERN = "sensor/{id}/control"  # For publishing to specific sensors

# Risk thresholds
MEDIUM_RISK_THRESHOLD = 40
HIGH_RISK_THRESHOLD = 60

sensor_alert_tracker = {}

def calculate_risk(temperature, humidity, co2, lum):
    """Calculate risk based on sensor values"""
    return (temperature * 0.55) - (humidity * 0.35) + (co2 * 0.05) + (lum * 0.05)

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    # Subscribe to all sensor-specific data topics
    mqtt_client.subscribe(MQTT_TOPIC_DATA_PATTERN)

def validate_sensor_data(data_values):
    validations = {
        'temperature': (-50, 100),   # Celsius
        'humidity': (0, 100),         # Percentual
        'co2': (0, 10000),            # ppm
        'luminosity': (0, 100000)     # lux
    }
    
    for key, (min_val, max_val) in validations.items():
        if key in data_values:
            value = data_values[key]
            if value < min_val or value > max_val:
                return False, key
    
    return True, None

def load_telegram_config():
    """Load Telegram configuration from database on startup"""
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)
        
        # Check if config table exists
        cursor.execute("SHOW TABLES LIKE 'config_telegram'")
        if not cursor.fetchone():
            print("Config table does not exist yet")
            return
        
        print("Telegram configurations loaded from database")
            
    except mysql.connector.Error as error:
        print(f"Error loading Telegram config: {error}")
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()
# Call this function before starting the app
load_telegram_config()

def send_telegram_message(user_id, message):
    """Send a Telegram message using a specific user's credentials"""
    try:
        # Get user's Telegram config from database
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)
        
        # Get token
        cursor.execute("SELECT valor FROM config_telegram WHERE chave = 'telegram_token' AND id_utilizador = %s", (user_id,))
        token_row = cursor.fetchone()
        if not token_row:
            print(f"No Telegram token found for user {user_id}")
            return False
        token = token_row['valor']
        
        # Get chat ID
        cursor.execute("SELECT valor FROM config_telegram WHERE chave = 'telegram_chat_id' AND id_utilizador = %s", (user_id,))
        chat_id_row = cursor.fetchone()
        if not chat_id_row:
            print(f"No Telegram chat ID found for user {user_id}")
            return False
        chat_id = chat_id_row['valor']
        
        # Close database connection
        cursor.close()
        connection.close()
        
        # Send message using this user's credentials
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print(f"Message sent successfully to Telegram for user {user_id}: {message}")
            return True
        else:
            print(f"Error sending message to Telegram for user {user_id}: {response.text}")
            return False
    except Exception as e:
        print(f"Error in Telegram request for user {user_id}: {e}")
        return False    
def check_and_notify_new_alert(sensor_id, alert_type, alert_value):
    """Check if this is the first alert of this type for this sensor and notify the appropriate users"""
    global sensor_alert_tracker
    
    if sensor_id not in sensor_alert_tracker:
        sensor_alert_tracker[sensor_id] = set()
    
    # Check if alert was already tracked
    is_new_alert = alert_type not in sensor_alert_tracker[sensor_id]
    
    # Get the users associated with this sensor and the area description
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    
    # Find all users associated with this sensor
    cursor.execute("""
        SELECT us.id_utilizador 
        FROM utilizadores_sensores us
        WHERE us.id_sensor = %s
    """, (sensor_id,))
    
    users = cursor.fetchall()
    
    # Get area description from areas_utilizador
    cursor.execute("""
        SELECT description 
        FROM areas_utilizador 
        WHERE sensor_id = %s
        LIMIT 1
    """, (sensor_id,))
    
    area_result = cursor.fetchone()
    area_description = area_result['description'] if area_result and area_result['description'] else "Área não especificada"
    
    # Only track the alert if we've notified at least one user
    notification_sent = False
    
    if is_new_alert:
        alert_type_text = {
            'temperature': 'Temperatura',
            'humidity': 'Humidade',
            'CO2': 'CO2',
            'luminosity': 'Luminosidade',
            'medium': 'Risco Médio',
            'high': 'Risco Alto'
        }.get(alert_type, alert_type)
        
        message = f"<b>ALERTA!</b> \n\n" \
                 f"Primeiro alerta de <b>{alert_type_text}</b> detectado\n" \
                 f"Sensor ID: <b>{sensor_id}</b>\n" \
                 f"Área: <b>{area_description}</b>\n" \
                 f"Horário: <b>{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</b>"
        
        # For each user, check if they have Telegram configured before sending
        for user in users:
            user_id = user['id_utilizador']
            
            # Check if this user has Telegram configured
            cursor.execute("""
                SELECT COUNT(*) as config_count 
                FROM config_telegram 
                WHERE id_utilizador = %s 
                AND chave IN ('telegram_token', 'telegram_chat_id')
            """, (user_id,))
            
            result = cursor.fetchone()
            if result and result['config_count'] >= 2:  # User has both token and chat_id configured
                # Send notification to this user
                if send_telegram_message(user_id, message):
                    notification_sent = True
    
    if is_new_alert and (notification_sent or not users):
        sensor_alert_tracker[sensor_id].add(alert_type)
    
    cursor.close()
    connection.close()
    
    return is_new_alert and notification_sent

def on_message(client, userdata, msg):
    try:
        print(f"DEBUG: MQTT Message Received on topic {msg.topic} -> {msg.payload}")

        if not msg.payload:
            print("Error: MQTT payload is empty!")
            return

        payload_str = msg.payload.decode()
        parts = payload_str.split("|")
        
        if len(parts) < 2:
            print(f"Error: Invalid message format: {payload_str}")
            return
        
        message_type = parts[0]
        
        # Handle data messages
        if "data" in msg.topic:
            if message_type == "0": # Regular data
                # Format: 0|id|timestamp|data
                if len(parts) < 4:
                    print(f"Error: Invalid data message format: {payload_str}")
                    return
                
                sensor_id = parts[1]
                timestamp = parts[2]
                data_str = parts[3]
                
                # Parse data values
                data_values = {}
                for item in data_str.split(","):
                    if "=" in item:
                        key, value = item.split("=")
                        data_values[key] = float(value)
                        
                # Validate sensor data first
                is_valid, invalid_key = validate_sensor_data(data_values)
                if not is_valid:
                    # Get area description for this sensor
                    connection = mysql.connector.connect(**db_config)
                    cursor = connection.cursor(dictionary=True)
                    cursor.execute("""
                        SELECT description 
                        FROM areas_utilizador 
                        WHERE sensor_id = %s
                        LIMIT 1
                    """, (sensor_id,))
                    area_result = cursor.fetchone()
                    area_description = area_result['description'] if area_result and area_result['description'] else "Área não especificada"
                    cursor.close()
                    connection.close()
                    
                    
                    parameter_translation = {
                        'temperature': 'Temperatura',
                        'humidity': 'Humidade',
                        'co2': 'CO2',
                        'luminosity': 'Luminosidade'
                    }
                    
                   
                    translated_param = parameter_translation.get(invalid_key, invalid_key)
                    
                    # Send to all users associated with this sensor
                    connection = mysql.connector.connect(**db_config)
                    cursor = connection.cursor(dictionary=True)
                    cursor.execute("SELECT id_utilizador FROM utilizadores_sensores WHERE id_sensor = %s", (sensor_id,))
                    users = cursor.fetchall()
                    cursor.close()
                    connection.close()
                    
                    for user in users:
                        send_telegram_message(user['id_utilizador'], error_message)
                    
                    # Save error to database
                    save_error_to_database(sensor_id, timestamp, invalid_key)
                    return
                                    
                # Calculate risk
                temp = data_values.get("temperature", 0)
                humidity = data_values.get("humidity", 0)
                co2 = data_values.get("co2", 0)
                lum = data_values.get("luminosity", 0)
                
                risk_value = calculate_risk(temp, humidity, co2, lum)
                        
                # Check if risk threshold is exceeded and save risk alert
                if risk_value >= HIGH_RISK_THRESHOLD:
                    check_and_notify_new_alert(sensor_id, "high", risk_value)
                    save_alert_to_database(sensor_id, "high", risk_value, timestamp)
                elif risk_value >= MEDIUM_RISK_THRESHOLD:
                    check_and_notify_new_alert(sensor_id, "medium", risk_value)
                    save_alert_to_database(sensor_id, "medium", risk_value, timestamp)
                
                # Save to database
                save_to_database(
                    sensor_id,
                    data_values.get("temperature"),
                    data_values.get("humidity"),
                    data_values.get("co2"),
                    data_values.get("luminosity"),
                    timestamp
                )
                
            elif message_type == "1":  # Alert messages
                # Format: 1|id|timestamp|value|type
                if len(parts) < 5:
                    print(f"Error: Invalid alert message format: {payload_str}")
                    return
                
                sensor_id = parts[1]
                timestamp = parts[2]
                alert_value = float(parts[3])
                alert_type = parts[4]
                
                # Add validation for alert values
                if alert_type in ['temperature', 'humidity', 'co2', 'luminosity']:
                    # Create a temporary data structure just for validation
                    temp_data = {alert_type: alert_value}
                    is_valid, invalid_key = validate_sensor_data(temp_data)
                    
                    if not is_valid:
                        # Get area description for this sensor
                        connection = mysql.connector.connect(**db_config)
                        cursor = connection.cursor(dictionary=True)
                        cursor.execute("""
                            SELECT description 
                            FROM areas_utilizador 
                            WHERE sensor_id = %s
                            LIMIT 1
                        """, (sensor_id,))
                        area_result = cursor.fetchone()
                        area_description = area_result['description'] if area_result and area_result['description'] else "Área não especificada"
                        cursor.close()
                        connection.close()
                        
                        # Handle invalid alert data as an error
                        parameter_translation = {
                            'temperature': 'Temperatura',
                            'humidity': 'Humidade',
                            'co2': 'CO2',
                            'luminosity': 'Luminosidade'
                        }
                        translated_param = parameter_translation.get(invalid_key, invalid_key)
                        
                        error_message = (f"❌ <b>ERRO DE VALIDAÇÃO!</b> ❌\n\n"
                                       f"Sensor ID: <b>{sensor_id}</b>\n"
                                       f"Área: <b>{area_description}</b>\n"
                                       f"Parâmetro inválido: <b>{translated_param}</b>\n"
                                       f"Horário: <b>{timestamp}</b>")
                        
                        # Send to all users associated with this sensor
                        connection = mysql.connector.connect(**db_config)
                        cursor = connection.cursor(dictionary=True)
                        cursor.execute("SELECT id_utilizador FROM utilizadores_sensores WHERE id_sensor = %s", (sensor_id,))
                        users = cursor.fetchall()
                        cursor.close()
                        connection.close()
                        
                        for user in users:
                            send_telegram_message(user['id_utilizador'], error_message)
                        
                        # Save error to database
                        save_error_to_database(sensor_id, timestamp, invalid_key)
                        return
                
                # Process valid alert as before
                print(f"Processing alert: Sensor ID={sensor_id}, Type={alert_type}, Value={alert_value}")
                save_alert_to_database(sensor_id, alert_type, alert_value, timestamp)
                check_and_notify_new_alert(sensor_id, alert_type, alert_value)
    
    except Exception as e:
        print(f"Unexpected error processing MQTT: {e}")

# Initialize MQTT client
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()
except Exception as e:
    print(f"Error connecting to MQTT broker: {e}")

def save_alert_to_database(sensor_id, alert_type, alert_value, timestamp=None):
    """Save alert to evento_sensor table"""
    connection = mysql.connector.connect(**db_config)
    if not connection:
        print("Error connecting to database.")
        return

    try:
        cursor = connection.cursor()
        
        # Map the sensor_type from message to the enum value in database
        if alert_type == "co2":
            alert_type_db = "CO2"
        else:
            alert_type_db = alert_type
            
        # Use provided timestamp or current time
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
        # Insert alert data
        insert_query = """
        INSERT INTO evento_sensor 
        (id_sensor, tipo_alerta, valor_alerta, evento_sensor_timestamp) 
        VALUES (%s, %s, %s, %s)
        """
        cursor.execute(insert_query, (
            sensor_id,
            alert_type_db,
            alert_value,
            timestamp
        ))

        connection.commit()
        print(f"Alert saved: Sensor ID={sensor_id}, Type={alert_type_db}, Value={alert_value}")
    except mysql.connector.Error as error:
        print(f"Error inserting alert: {error}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def save_error_to_database(sensor_id, timestamp=None, error_type=None):
    connection = mysql.connector.connect(**db_config)
    if not connection:
        print("Error connecting to database.")
        return
    try:
        cursor = connection.cursor()
       
        # Use provided timestamp or current time
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
           
        insert_query = """
        INSERT INTO erros (timestamp, sensor_id, error_type)
        VALUES (%s, %s, %s)
        """
        cursor.execute(insert_query, (timestamp, sensor_id, error_type))
        connection.commit()
        print(f"Error recorded in database for sensor {sensor_id}, type: {error_type}")
    except mysql.connector.Error as error:
        print(f"Error inserting error in database: {error}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def save_to_database(sensor_id, temperature=None, humidity=None, co2=None, luminosity=None, timestamp=None):
    connection = mysql.connector.connect(**db_config)
    if not connection:
        print("Error connecting to database.")
        return

    try:
        cursor = connection.cursor()
        
        # Use provided timestamp or current time
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
        # Insert data with updated schema
        insert_query = """
        INSERT INTO dados 
        (timestamp, sensor_id, temperature, humidity, CO2, Lum) 
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_query, (
            timestamp,
            sensor_id,
            temperature,
            humidity,
            co2,
            luminosity
        ))

        connection.commit()
        print(f"Data saved: Temp={temperature}, Humidity={humidity}, CO2={co2}, Luminosity={luminosity}")
    except mysql.connector.Error as error:
        print(f"Error inserting data: {error}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def update_sensor_status(sensor_id, status):
    """Update the status of a sensor in the database"""
    connection = None
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor()
        
        update_query = """
        UPDATE sistemas_sensores 
        SET status = %s 
        WHERE id = %s
        """
        cursor.execute(update_query, (status, sensor_id))
        connection.commit()
        
        print(f"Status updated for sensor {sensor_id} to {status}")
        return True
    except mysql.connector.Error as error:
        print(f"Error updating sensor status: {error}")
        return False
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/reset-alert-tracker', methods=['POST'])
def reset_alert_tracker():
    global sensor_alert_tracker
    try:
        data = request.json
        sensor_id = data.get('sensor_id')
        
        if sensor_id:
            if sensor_id in sensor_alert_tracker:
                sensor_alert_tracker[sensor_id] = set()
                return jsonify({"message": f"Alert tracker reset for sensor {sensor_id}"}), 200
            else:
                return jsonify({"message": f"No tracked alerts for sensor {sensor_id}"}), 200
        else:
            sensor_alert_tracker = {}
            return jsonify({"message": "Alert tracker reset for all sensors"}), 200
            
    except Exception as e:
        return jsonify({"error": f"Error resetting alert tracker: {str(e)}"}), 500

@app.route('/start', methods=['POST'])
def start_generator():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        generator_id = request.json.get("sensor_id")
        sampling_time = str(data.get('sampling_time'))

        if not generator_id or not sampling_time:
            return jsonify({"error": "Generator ID or sampling time not provided!"}), 400

        # Validate sampling time is a number
        try:
            float(sampling_time)
        except ValueError:
            return jsonify({"error": "Sampling time must be a number"}), 400

        # Construct MQTT command with the correct format: 0|id|time
        command = f"0|{generator_id}|{sampling_time}"
        
        # Generate the specific control topic for this sensor
        sensor_control_topic = MQTT_TOPIC_CONTROL_PATTERN.format(id=generator_id)
        
        # Publish to both the general topic and the specific sensor topic
        mqtt_client.publish(MQTT_TOPIC_CONTROL_PATTERN, command)  # For backward compatibility
        mqtt_client.publish(sensor_control_topic, command)  # For the new specific topic
        
        print(f"Published to {MQTT_TOPIC_CONTROL_PATTERN} and {sensor_control_topic}: {command}")
        
        # Update sensor status to 'running'
        update_sensor_status(generator_id, 'running')

        return jsonify({"message": f"Start command sent for generator {generator_id} with sampling time {sampling_time} seconds."}), 200

    except Exception as e:
        print(f"Unexpected error in start_generator: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
    
@app.route('/create_user', methods=['POST'])
def create_user():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        nome = data.get('nome')
        password = data.get('password')
        permissao = data.get('permissao')

        if not nome or not password or not permissao:
            return jsonify({"error": "Missing user data"}), 400

        # Connect to database
        connection = mysql.connector.connect(**db_config)
        if not connection:
            return jsonify({"error": "Database connection error"}), 500

        try:
            cursor = connection.cursor()
            
            # Check if user already exists
            cursor.execute(
                "SELECT * FROM utilizadores WHERE nome = %s", 
                (nome,)
            )
            
            if cursor.fetchone():
                cursor.close()
                connection.close()
                return jsonify({"error": "User already exists"}), 400
            
            # Insert new user
            cursor.execute(
                "INSERT INTO utilizadores (nome, password, permissao) VALUES (%s, %s, %s)",
                (nome, password, permissao)
            )
            
            connection.commit()
            cursor.close()
            connection.close()
            
            return jsonify({"message": f"User {nome} created successfully"}), 200
            
        except mysql.connector.Error as error:
            print(f"Error creating user: {error}")
            if connection.is_connected():
                cursor.close()
                connection.close()
            return jsonify({"error": f"Database error: {str(error)}"}), 500

    except Exception as e:
        print(f"Unexpected error in create_user: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/associate_user_sensor', methods=['POST'])
def associate_user_sensor():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        user_id = data.get('user_id')
        sensor_id = data.get('sensor_id')

        if not user_id or not sensor_id:
            return jsonify({"error": "User ID or Sensor ID not provided!"}), 400

        # Connect to database
        connection = mysql.connector.connect(**db_config)
        if not connection:
            return jsonify({"error": "Database connection error"}), 500

        try:
            cursor = connection.cursor()
            
            # Check if association already exists
            cursor.execute(
                "SELECT * FROM utilizadores_sensores WHERE id_utilizador = %s AND id_sensor = %s", 
                (user_id, sensor_id)
            )
            
            if cursor.fetchone():
                cursor.close()
                connection.close()
                return jsonify({"error": "This association already exists"}), 400
            
            # Insert new association
            cursor.execute(
                "INSERT INTO utilizadores_sensores (id_utilizador, id_sensor) VALUES (%s, %s)",
                (user_id, sensor_id)
            )
            
            connection.commit()
            cursor.close()
            connection.close()
            
            return jsonify({"message": f"User {user_id} associated with sensor {sensor_id} successfully"}), 200
            
        except mysql.connector.Error as error:
            print(f"Error associating user with sensor: {error}")
            if connection.is_connected():
                cursor.close()
                connection.close()
            return jsonify({"error": f"Database error: {str(error)}"}), 500

    except Exception as e:
        print(f"Unexpected error in associate_user_sensor: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
    
@app.route('/disassociate_user_sensor', methods=['POST'])
def disassociate_user_sensor():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        user_id = data.get('user_id')
        sensor_id = data.get('sensor_id')

        if not user_id or not sensor_id:
            return jsonify({"error": "User ID or Sensor ID not provided!"}), 400

        # Connect to database
        connection = mysql.connector.connect(**db_config)
        if not connection:
            return jsonify({"error": "Database connection error"}), 500

        try:
            cursor = connection.cursor()

            # Check if association exists
            cursor.execute(
                "SELECT * FROM utilizadores_sensores WHERE id_utilizador = %s AND id_sensor = %s",
                (user_id, sensor_id)
            )

            if not cursor.fetchone():
                cursor.close()
                connection.close()
                return jsonify({"error": "This association does not exist"}), 404

            # Delete the association
            cursor.execute(
                "DELETE FROM utilizadores_sensores WHERE id_utilizador = %s AND id_sensor = %s",
                (user_id, sensor_id)
            )

            connection.commit()
            cursor.close()
            connection.close()

            return jsonify({"message": f"User {user_id} disassociated from sensor {sensor_id} successfully"}), 200

        except mysql.connector.Error as error:
            print(f"Error disassociating user from sensor: {error}")
            if connection.is_connected():
                cursor.close()
                connection.close()
            return jsonify({"error": f"Database error: {str(error)}"}), 500

    except Exception as e:
        print(f"Unexpected error in disassociate_user_sensor: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/end', methods=['POST'])
def end_generator():
    try:
        data = request.json
        generator_id = str(data.get('generator_id'))

        if not generator_id:
            return jsonify({"error": "Generator ID not provided!"}), 400

        # Construct command
        command = f"1|{generator_id}"
        
        # Generate the specific control topic for this sensor
        sensor_control_topic = MQTT_TOPIC_CONTROL_PATTERN.format(id=generator_id)
        
        # Send to both topics
        mqtt_client.publish(MQTT_TOPIC_CONTROL_PATTERN, command)  # For backward compatibility
        mqtt_client.publish(sensor_control_topic, command)  # For the new specific topic
        
        print(f"Published to {MQTT_TOPIC_CONTROL_PATTERN} and {sensor_control_topic}: {command}")
        
        # Update sensor status to 'stopped'
        update_sensor_status(generator_id, 'stopped')
        
        # Return HTTP response
        return jsonify({"message": f"End signal sent for generator {generator_id}."}), 200
        
    except Exception as e:
        print(f"Unexpected error in end_generator: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/eventos-data', methods=['GET'])
def get_events_data():
    """Get all sensor events from the database for frontend display"""
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)
        
        query = """
        SELECT id_evento_sensor, evento_sensor_timestamp, id_sensor, tipo_alerta, valor_alerta 
        FROM evento_sensor 
        ORDER BY evento_sensor_timestamp DESC
        LIMIT 100
        """
        
        cursor.execute(query)
        events = cursor.fetchall()
        
        for event in events:
            # Convert timestamp to string for JSON serialization
            event['evento_sensor_timestamp'] = event['evento_sensor_timestamp'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify(events)
        
    except mysql.connector.Error as error:
        print(f"Database error: {error}")
        return jsonify({"error": f"Database error: {str(error)}"}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/alerts', methods=['GET'])
def get_alerts():
    """Get all sensor alerts from the database"""
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)
        
        query = """
        SELECT id_evento_sensor, evento_sensor_timestamp, id_sensor, tipo_alerta, valor_alerta 
        FROM evento_sensor 
        ORDER BY evento_sensor_timestamp DESC
        LIMIT 100
        """
        
        cursor.execute(query)
        alerts = cursor.fetchall()
        
        for alert in alerts:
            # Convert timestamp to string for JSON serialization
            alert['evento_sensor_timestamp'] = alert['evento_sensor_timestamp'].strftime("%Y-%m-%d %H:%M:%S")
        
        return jsonify({"alerts": alerts}), 200
        
    except mysql.connector.Error as error:
        print(f"Database error: {error}")
        return jsonify({"error": f"Database error: {str(error)}"}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/delete_sensor', methods=['POST'])
def delete_sensor():
    try:
        data = request.json
        sensor_id = data.get('sensor_id')
        if not sensor_id:
            return jsonify({"error": "Sensor ID not provided"}), 400

        # Connect to the database
        connection = mysql.connector.connect(**db_config)
        if not connection:
            return jsonify({"error": "Database connection error"}), 500

        try:
            cursor = connection.cursor()

            # Check if the sensor exists
            cursor.execute("SELECT id FROM sistemas_sensores WHERE id = %s", (sensor_id,))
            if not cursor.fetchone():
                cursor.close()
                connection.close()
                return jsonify({"error": f"Sensor with ID {sensor_id} not found"}), 404

            # Delete associations in utilizadores_sensores table
            cursor.execute("DELETE FROM utilizadores_sensores WHERE id_sensor = %s", (sensor_id,))

            # Delete data in dados table
            cursor.execute("DELETE FROM dados WHERE sensor_id = %s", (sensor_id,))

            # Delete alerts in evento_sensor table
            cursor.execute("DELETE FROM evento_sensor WHERE id_sensor = %s", (sensor_id,))

            # Delete errors in erros table
            cursor.execute("DELETE FROM erros WHERE sensor_id = %s", (sensor_id,))

            # Finally, delete the sensor itself
            cursor.execute("DELETE FROM sistemas_sensores WHERE id = %s", (sensor_id,))

            connection.commit()
            cursor.close()
            connection.close()
            return jsonify({"message": f"Sensor {sensor_id} deleted successfully"}), 200

        except mysql.connector.Error as error:
            print(f"Error deleting sensor: {error}")
            if connection.is_connected():
                connection.rollback()
                cursor.close()
                connection.close()
            return jsonify({"error": f"Database error: {str(error)}"}), 500

    except Exception as e:
        print(f"Unexpected error in delete_sensor: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/update_telegram_config', methods=['POST'])
def update_telegram_config():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        token = data.get('token')
        chat_id = data.get('chat_id')
        user_id = data.get('user_id')

        if not token or not chat_id or not user_id:
            return jsonify({"error": "Token, Chat ID, or User ID not provided!"}), 400
        
        # Connect to database
        connection = mysql.connector.connect(**db_config)
        if not connection:
            return jsonify({"error": "Database connection error"}), 500

        try:
            cursor = connection.cursor()
            
            # Check if an entry exists for this user and key
            cursor.execute("SELECT id FROM config_telegram WHERE chave = 'telegram_token' AND id_utilizador = %s", (user_id,))
            if cursor.fetchone():
                # Update existing entry
                cursor.execute(
                    "UPDATE config_telegram SET valor = %s WHERE chave = 'telegram_token' AND id_utilizador = %s",
                    (token, user_id)
                )
            else:
                # Insert new entry
                cursor.execute(
                    "INSERT INTO config_telegram (chave, valor, id_utilizador) VALUES ('telegram_token', %s, %s)",
                    (token, user_id)
                )
            
            # Check if an entry exists for this user and key
            cursor.execute("SELECT id FROM config_telegram WHERE chave = 'telegram_chat_id' AND id_utilizador = %s", (user_id,))
            if cursor.fetchone():
                # Update existing entry
                cursor.execute(
                    "UPDATE config_telegram SET valor = %s WHERE chave = 'telegram_chat_id' AND id_utilizador = %s",
                    (chat_id, user_id)
                )
            else:
                # Insert new entry
                cursor.execute(
                    "INSERT INTO config_telegram (chave, valor, id_utilizador) VALUES ('telegram_chat_id', %s, %s)",
                    (chat_id, user_id)
                )
            
            connection.commit()
            cursor.close()
            connection.close()
            
            # Send test message
            test_message = "<b>✅ Configuração concluída!</b>\n\nO sistema está agora conectado ao Telegram."
            success = send_telegram_message(user_id, test_message)
            
            if success:
                return jsonify({"message": "Telegram configuration saved and tested successfully"}), 200
            else:
                return jsonify({"error": "Configuration saved but test message failed"}), 500
            
        except mysql.connector.Error as error:
            print(f"Database error: {error}")
            if connection.is_connected():
                cursor.close()
                connection.close()
            return jsonify({"error": f"Database error: {str(error)}"}), 500

    except Exception as e:
        print(f"Unexpected error in update_telegram_config: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
        
@app.route('/delete_user', methods=['POST'])
def delete_user():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        username = data.get('username')

        if not username:
            return jsonify({"error": "Username not provided!"}), 400

        # Connect to the database
        connection = mysql.connector.connect(**db_config)
        if not connection:
            return jsonify({"error": "Database connection error"}), 500

        try:
            cursor = connection.cursor()

            # Check if user exists
            cursor.execute("SELECT id FROM utilizadores WHERE nome = %s", (username,))
            user = cursor.fetchone()
            
            if not user:
                cursor.close()
                connection.close()
                return jsonify({"error": f"User with name '{username}' not found"}), 404

            user_id = user[0]
            
            # Delete any associations
            cursor.execute("DELETE FROM utilizadores_sensores WHERE id_utilizador = %s", (user_id,))

            # Delete the user
            cursor.execute("DELETE FROM utilizadores WHERE id = %s", (user_id,))

            connection.commit()
            cursor.close()
            connection.close()
            return jsonify({"message": f"User '{username}' deleted successfully"}), 200

        except mysql.connector.Error as error:
            print(f"Error deleting user: {error}")
            if connection.is_connected():
                connection.rollback()
                cursor.close()
                connection.close()
            return jsonify({"error": f"Database error: {str(error)}"}), 500

    except Exception as e:
        print(f"Unexpected error in delete_user: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/update_user_image', methods=['POST'])
def update_user_image():
    """Atualiza ou insere a imagem do usuário no banco de dados"""
    if not request.json:
        return jsonify({"error": "Dados inválidos"}), 400
    
    user_id = request.json.get('user_id')
    image_path = request.json.get('image_path')
    
    if not user_id or not image_path:
        return jsonify({"error": "ID do usuário ou caminho da imagem não fornecidos"}), 400
    
    try:
        # Verificar se o utilizador já possui uma imagem
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM utilizador_imagem WHERE user_id = %s", (user_id,))
        existing_image = cursor.fetchone()
        
        if existing_image:
            # Atualizar imagem existente
            cursor.execute(
                "UPDATE utilizador_imagem SET image_path = %s, updated_at = NOW() WHERE user_id = %s",
                (image_path, user_id)
            )
        else:
            # Inserir nova imagem
            cursor.execute(
                "INSERT INTO utilizador_imagem (user_id, image_path, created_at) VALUES (%s, %s, NOW())",
                (user_id, image_path)
            )
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({"message": "Imagem atualizada com sucesso"}), 200
        
    except Error as e:
        print(f"Database error: {e}")
        return jsonify({"error": f"Erro no banco de dados: {str(e)}"}), 500

@app.route('/save_user_area', methods=['POST'])
def save_user_area():
    """Salva uma área selecionada pelo usuário com sensor associado"""
    if not request.json:
        return jsonify({"error": "Dados inválidos"}), 400
    
    user_id = request.json.get('user_id')
    x = request.json.get('x')
    y = request.json.get('y')
    width = request.json.get('width')
    height = request.json.get('height')
    description = request.json.get('description', '')
    sensor_id = request.json.get('sensor_id')
    sensor_x = request.json.get('sensor_x')
    sensor_y = request.json.get('sensor_y')
    
    if not all([user_id, x is not None, y is not None, width is not None, height is not None]):
        return jsonify({"error": "Parâmetros incompletos"}), 400
    
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor()
        
        cursor.execute(
            """
            INSERT INTO areas_utilizador 
            (user_id, x, y, sensor_x, sensor_y, width, height, description, sensor_id, created_at) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (user_id, x, y, sensor_x, sensor_y, width, height, description, sensor_id)
        )
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({"message": "Área salva com sucesso"}), 200
        
    except Error as e:
        print(f"Database error: {e}")
        return jsonify({"error": f"Erro no banco de dados: {str(e)}"}), 500

# API adicional para listar sensores disponíveis
@app.route('/get_available_sensors', methods=['GET'])
def get_available_sensors():
    """Retorna a lista de sensores disponíveis para um usuário específico"""
    user_id = request.args.get('user_id')
    
    if not user_id:
        return jsonify({"error": "ID do usuário não fornecido"}), 400
        
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT s.id, s.status 
            FROM sistemas_sensores s
            JOIN utilizadores_sensores us ON s.id = us.id_sensor
            WHERE us.id_utilizador = %s
            AND s.id NOT IN (
                SELECT sensor_id FROM areas_utilizador WHERE sensor_id IS NOT NULL
            )           
        """, (user_id,))
        
        sensors = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        return jsonify({"sensors": sensors}), 200
        
    except Error as e:
        print(f"Database error: {e}")
        return jsonify({"error": f"Erro no banco de dados: {str(e)}"}), 500
    
@app.route('/delete_user_area', methods=['POST'])
def delete_user_area():
    if not request.json:
        return jsonify({"error": "Dados inválidos"}), 400
    
    area_id = request.json.get('area_id')
    user_id = request.json.get('user_id')
    
    if not area_id or not user_id:
        return jsonify({"error": "ID da área ou ID do usuário não fornecido"}), 400
    
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor()
        
        # Verificar se a área pertence ao utilizador (segurança)
        cursor.execute(
            "SELECT id FROM areas_utilizador WHERE id = %s AND user_id = %s",
            (area_id, user_id)
        )
        
        if cursor.fetchone() is None:
            cursor.close()
            connection.close()
            return jsonify({"error": "Área não encontrada ou acesso negado"}), 403
        
        cursor.execute("DELETE FROM areas_utilizador WHERE id = %s", (area_id,))
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({"message": "Área excluída com sucesso"}), 200
        
    except Error as e:
        print(f"Database error: {e}")
        return jsonify({"error": f"Erro no banco de dados: {str(e)}"}), 500
    

@app.route('/clear_user_areas', methods=['POST'])
def clear_user_areas():
    """Remove todas as áreas associadas a um usuário"""
    if not request.json:
        return jsonify({"error": "Dados inválidos"}), 400
    
    user_id = request.json.get('user_id')
    
    if not user_id:
        return jsonify({"error": "ID do usuário não fornecido"}), 400
    
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor()
        
        cursor.execute("DELETE FROM areas_utilizador WHERE user_id = %s", (user_id,))
        connection.commit()
        cursor.close()
        connection.close()
        
        return jsonify({"message": "Áreas limpas com sucesso"}), 200
        
    except Error as e:
        print(f"Database error: {e}")
        return jsonify({"error": f"Erro no banco de dados: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)  