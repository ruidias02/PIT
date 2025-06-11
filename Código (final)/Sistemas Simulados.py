import paho.mqtt.client as mqtt
import random
import time
import sys
import threading
import json
from datetime import datetime
import mysql.connector
import os

# MQTT Configuration
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC_DATA_PATTERN = "sensor/{id}/data"
MQTT_TOPIC_CONTROL_PATTERN = "sensor/{id}/control"

# Set up empty specific topic variables (will be populated once generator ID is known)


# Generator ID is now assigned during runtime via MQTT
GENERATOR_ID = "4"
SAMPLING_TIME = None
MQTT_TOPIC_DATA = MQTT_TOPIC_DATA_PATTERN.format(id=GENERATOR_ID)
MQTT_TOPIC_CONTROL = MQTT_TOPIC_CONTROL_PATTERN.format(id=GENERATOR_ID)


CSV_FILE_PATH = os.path.join("data", f"{GENERATOR_ID}.txt")
# Control flags
SEND_DATA = False  # Start as False, only turn on when commanded
EXIT_PROGRAM = False
DATA_INITIALIZED = False  # Flag to indicate if data has been loaded

# Store current values for sensor readings
current_values = {
    "temperature": 25.0,  # Default initial values to avoid NoneType errors
    "humidity": 50.0,
    "co2": 400.0,
    "luminosity": 500.0
}

# Previous values for alert comparison
previous_values = {
    "temperature": None,
    "humidity": None,
    "co2": None,
    "luminosity": None
}

# Timestamp of last alert sent for each sensor
last_alert_time = {
    "temperature": 0,
    "humidity": 0,
    "co2": 0,
    "luminosity": 0
}

# Índice para leitura sequencial
current_line_index = 0
# Armazenar todas as linhas do arquivo
file_data = []

# Lock for thread-safe access to current_values
values_lock = threading.Lock()
# Lock for thread-safe access to file_data and current_line_index
file_lock = threading.Lock()

def on_connect(client, userdata, flags, rc):
    global MQTT_TOPIC_CONTROL
    print(f"Connected to MQTT broker with result code {rc}")
    
    # If generator ID is already known, subscribe to its specific control topic
    if GENERATOR_ID is not None:
        MQTT_TOPIC_CONTROL = MQTT_TOPIC_CONTROL_PATTERN.format(id=GENERATOR_ID)
        client.subscribe(MQTT_TOPIC_CONTROL)
        print(f"Subscribed to control topic: {MQTT_TOPIC_CONTROL}")
    else:
        # If not yet known, subscribe to the general control topic to receive initialization
        client.subscribe("sensors/control")
        print("Subscribed to general control topic waiting for ID assignment")

def on_message(client, userdata, msg):
    global SEND_DATA, EXIT_PROGRAM, DATA_INITIALIZED
    global GENERATOR_ID, SAMPLING_TIME, MQTT_TOPIC_DATA, MQTT_TOPIC_CONTROL
    
    try:
        command = msg.payload.decode()
        command_parts = command.split("|")
        command_type = command_parts[0]
        
        print(f"Received command: {command}")
        
        if command_type == "0":  # Start command
            if len(command_parts) == 3:
                target_id = command_parts[1]
                sampling_time_received = float(command_parts[2])
                
                # If this is the first message setting up the ID
                if GENERATOR_ID is None:
                    GENERATOR_ID = target_id
                    SAMPLING_TIME = sampling_time_received
                    
                    # Now that we have an ID, set up the specific topics
                    MQTT_TOPIC_DATA = MQTT_TOPIC_DATA_PATTERN.format(id=GENERATOR_ID)
                    MQTT_TOPIC_CONTROL = MQTT_TOPIC_CONTROL_PATTERN.format(id=GENERATOR_ID)
                    
                    # Unsubscribe from the general topic and subscribe to the specific one
                    client.unsubscribe("sensors/control")
                    client.subscribe(MQTT_TOPIC_CONTROL)
                    print(f"Initialized as generator {GENERATOR_ID} with topics:")
                    print(f"Data: {MQTT_TOPIC_DATA}")
                    print(f"Control: {MQTT_TOPIC_CONTROL}")
                        
                elif GENERATOR_ID == target_id:
                    # Only respond if it's addressed to this specific generator
                    SAMPLING_TIME = sampling_time_received
                    print(f"Generator {GENERATOR_ID} updating sampling time to {SAMPLING_TIME} seconds")
                
                SEND_DATA = True

        elif command_type == "1" and GENERATOR_ID is not None:
            if len(command_parts) == 2:
                target_id = command_parts[1]
                if target_id == GENERATOR_ID:
                    # Stop sending data
                    SEND_DATA = False
                    print(f"Generator {GENERATOR_ID} stopped sending data but continues monitoring")

    except Exception as e:
        print(f"Error processing message: {e}")

# Função para carregar todos os dados do arquivo
def load_all_file_data(csv_file_path=None):
    global file_data, GENERATOR_ID, CSV_FILE_PATH
    
    if csv_file_path is None:
        csv_file_path = os.path.join("data", f"{GENERATOR_ID}.txt")
    
    try:
        with file_lock:
            file_data = []  # Limpar dados anteriores
            
            if not os.path.exists(csv_file_path):
                print(f"Error: File not found at {csv_file_path}")
                return False
            
            with open(csv_file_path, 'r') as file:
                # Ler todas as linhas do arquivo
                lines = file.readlines()
                
                # Converter linhas para dicionários
                for line in lines:
                    # Remover espaços em branco e dividir por vírgula
                    values = line.strip().split(',')
                    
                    # Garantir que temos exatamente 4 valores
                    if len(values) == 4:
                        try:
                            record = {
                                'temperature': float(values[0]),
                                'humidity': float(values[1]),
                                'co2': float(values[2]),
                                'luminosity': float(values[3])
                            }
                            file_data.append(record)
                        except ValueError as e:
                            print(f"Erro ao converter linha {line}: {e}")
                
            if not file_data:
                print("Warning: No valid data found in the file")
                return False
            
            print(f"Successfully loaded {len(file_data)} records from {csv_file_path}")
            return True
    
    except Exception as e:
        print(f"Error loading data from {csv_file_path}: {e}")
        return False

# Função para obter o próximo registo na sequência
def fetch_next_sensor_data():
    global current_line_index, file_data, GENERATOR_ID, DATA_INITIALIZED
    
    with file_lock:
        # Verificar se temos dados carregados
        if not file_data:
            # Tentar carregar os dados
            sensor_file_path = os.path.join("data", f"{GENERATOR_ID}.txt")
            success = load_all_file_data(sensor_file_path)
            if not success or not file_data:
                print("No data available, using default values")
                return default_sensor_values()
        
        # Verificar se o índice está dentro dos limites
        if current_line_index >= len(file_data):
            # Voltar ao início do arquivo
            current_line_index = 0
            print("Reached end of file, restarting from beginning")
        
        # Obter o registo atual
        record = file_data[current_line_index]
        print(f"Reading line {current_line_index + 1} of {len(file_data)}: {record}")
        
        # Incrementar o índice para a próxima leitura
        current_line_index += 1
        
        # Marcar que os dados foram inicializados
        DATA_INITIALIZED = True
        
        # Verificar se o registo tem potencial erro (mantido do código original)
        record = generate_potential_error_data(record)
        
        return record

def default_sensor_values():
    return {
        "temperature": 25.0,
        "humidity": 50.0,
        "co2": 400.0,
        "luminosity": 500.0
    }

# Check if an alert condition is met
def check_alert_condition(sensor_type, value):
    global previous_values
    
    # Check if the value is None before attempting comparisons
    if value is None:
        return False
    
    if sensor_type == "temperature":
        return value > 45.0
    elif sensor_type == "humidity":
        return value < 30.0
    elif sensor_type == "co2":
        return value > 10000.0
    elif sensor_type == "luminosity":
        return value > 500.0
    return False

# Send alert message via MQTT
def send_alert(mqtt_client, sensor_type, value):
    global GENERATOR_ID, last_alert_time, MQTT_TOPIC_DATA
    
    try:
        # Make sure we have a valid topic
        if MQTT_TOPIC_DATA is None:
            print("Cannot send alert: data topic not yet initialized")
            return False
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Format for alert messages: code 1 indicates alert
        alert_message = f"1|{GENERATOR_ID}|{timestamp}|{value}|{sensor_type}"
        
        print(f"ALERT: Sending alert for {sensor_type} = {value}")
        mqtt_client.publish(MQTT_TOPIC_DATA, alert_message)
        
        # Update timestamp of last alert sent
        last_alert_time[sensor_type] = time.time()
        return True
    except Exception as e:
        print(f"Error sending alert: {e}")
        return False

# Function to continuously monitor data at exactly 1-second intervals
def monitor_data(mqtt_client):
    global current_values, previous_values, last_alert_time, EXIT_PROGRAM
    global SEND_DATA, DATA_INITIALIZED
    
    try:
        print("Starting initial data load...")
        sensor_file_path = os.path.join("data", f"{GENERATOR_ID}.txt")
        success = load_all_file_data(sensor_file_path)
        if success:
            with values_lock:
                current_values = fetch_next_sensor_data()
                print(f"Initial data loaded: {current_values}")
        else:
            print("Failed to load initial data, will use defaults")
    except Exception as e:
        print(f"Error during initial data load: {e}")
    
    # Immediately start fetching and updating data
    while not EXIT_PROGRAM:
        next_update_time = time.time() + 1.0  # Schedule exactly 1 second from now
        
        try:
            # Fetch new sensor data every second, agora sequencialmente
            with values_lock:
                current_values = fetch_next_sensor_data()
        except Exception as e:
            print(f"Error fetching sensor data: {e}")
            continue

        # Print timestamp and current data every second
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            values_str = " | ".join(f"{sensor}: {value}" for sensor, value in current_values.items())
            print(f"Timestamp: {timestamp} | {values_str}")
        except Exception as e:
            print(f"Error accessing current_values: {e}")
        
        # Check for alerts only when data is initialized
        if DATA_INITIALIZED:
            with values_lock:
                # Check temperature alert
                if check_alert_condition("temperature", current_values["temperature"]):
                    send_alert(mqtt_client, "temperature", current_values["temperature"])
                
                # Check humidity alert
                if check_alert_condition("humidity", current_values["humidity"]):
                    send_alert(mqtt_client, "humidity", current_values["humidity"])
                
                # Check CO2 alert
                if check_alert_condition("co2", current_values["co2"]):
                    send_alert(mqtt_client, "co2", current_values["co2"])
                
                # Check luminosity alert
                if check_alert_condition("luminosity", current_values["luminosity"]):
                    send_alert(mqtt_client, "luminosity", current_values["luminosity"])
                
                # Update previous values for next comparison
                for sensor in current_values:
                    if current_values[sensor] is not None:
                        previous_values[sensor] = current_values[sensor]
        
        # Calculate how long to sleep to maintain exactly 1 second intervals
        sleep_time = next_update_time - time.time()
        if sleep_time > 0:
            time.sleep(sleep_time)
            
def generate_potential_error_data(data_values):
    import random
    import copy

    # Create a copy of the data to avoid modifying the original
    modified_data = copy.deepcopy(data_values)
    
    # chance of generating an error value
    if random.random() < 0.05:
        # Define error ranges based on original parameters
        error_ranges = {
            'temperature': (100, 200),  
            'humidity': (100, 150),      
            'co2': (10000, 20000),      
            'luminosity': (100000, 200000)
        }
        
        # Randomly choose a parameter to force an error
        error_key = random.choice(list(modified_data.keys()))
        
        # Generate a value above the limit for that parameter
        error_range = error_ranges[error_key]
        modified_data[error_key] = random.uniform(error_range[0], error_range[1])
        
        print(f"SIMULATED ERROR: {error_key} generated with value {modified_data[error_key]}")
    
    return modified_data

def send_data_periodically(mqtt_client):
    global EXIT_PROGRAM, SAMPLING_TIME, GENERATOR_ID, current_values, MQTT_TOPIC_DATA, SEND_DATA
    
    while not EXIT_PROGRAM:
        # Wait until SEND_DATA is True
        while not SEND_DATA and not EXIT_PROGRAM:
            time.sleep(0.5)
        
        if EXIT_PROGRAM:
            break
        
        # Ensure we have valid parameters
        if SAMPLING_TIME is None or MQTT_TOPIC_DATA is None:
            time.sleep(1.0)
            continue
            
        next_send_time = time.time() + SAMPLING_TIME
        
        # Format timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
        # Construct message in the required format: code|id|timestamp|data
        with values_lock:
            data_str = f"0|{GENERATOR_ID}|{timestamp}|"
            data_str += f"temperature={current_values['temperature']},"
            data_str += f"humidity={current_values['humidity']},"
            data_str += f"co2={current_values['co2']},"
            data_str += f"luminosity={current_values['luminosity']}"
                
        print(f"DEBUG: Sending to MQTT -> {data_str}")
        mqtt_client.publish(MQTT_TOPIC_DATA, data_str)
        
        # Calculate time to sleep to maintain exact intervals
        sleep_time = next_send_time - time.time()
        if sleep_time > 0:
            time.sleep(sleep_time)

def main():
    # Initialize global variables
    global EXIT_PROGRAM, GENERATOR_ID, SAMPLING_TIME
    
    # Criar o diretório de dados se não existir
    os.makedirs("data", exist_ok=True)
    
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        
        print("Starting program, checking for data file...")
        
        # Verificar se o arquivo de dados existe e criar um arquivo de exemplo se não existir
        data_file_path = os.path.join("data", f"{GENERATOR_ID}.txt")
        if not os.path.exists(data_file_path):
            print(f"Data file {data_file_path} not found, creating example file")
            with open(data_file_path, 'w') as f:
                # Criar alguns dados de exemplo
                for i in range(20):
                    temp = 20 + i
                    hum = 50 - i
                    co2 = 400 + (i * 10)
                    lum = 500 + (i * 15)
                    f.write(f"{temp},{hum},{co2},{lum}\n")
            print(f"Created example data file with 20 records")
        
        # Start thread for monitoring alerts (every 1 second)
        monitor_thread = threading.Thread(target=monitor_data, args=(client,))
        monitor_thread.daemon = True
        monitor_thread.start()
        
        # Main data sending thread (at sampling interval)
        send_data_thread = threading.Thread(target=send_data_periodically, args=(client,))
        send_data_thread.daemon = True
        send_data_thread.start()
        
        # Main thread just waits for EXIT_PROGRAM to be set
        while not EXIT_PROGRAM:
            time.sleep(1)
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        EXIT_PROGRAM = True
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()