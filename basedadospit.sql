DROP DATABASE sistema_dados;
CREATE DATABASE sistema_dados;
-- Usar a base de dados
USE sistema_dados;

CREATE TABLE sistemas_sensores (
    id INT PRIMARY KEY auto_increment,
    status ENUM('running', 'stopped') DEFAULT 'stopped'
);

SET SESSION sql_mode='NO_AUTO_VALUE_ON_ZERO';

-- Insere o registro com id = 0
INSERT INTO sistemas_sensores (id) VALUES (0);

-- Restaura o modo SQL para o padrão (opcional)
SET SESSION sql_mode='';
CREATE TABLE dados (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME NOT NULL,
    sensor_id INT,
    temperature FLOAT NULL,
    humidity FLOAT NULL,
    CO2 FLOAT NULL,
    Lum FLOAT NULL,
    FOREIGN KEY (sensor_id) REFERENCES sistemas_sensores(id)
);

CREATE TABLE erros (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME NOT NULL,
    sensor_id INT NOT NULL,
    
	FOREIGN KEY (sensor_id) REFERENCES sistemas_sensores(id)
);

ALTER TABLE erros ADD COLUMN error_type VARCHAR(255);
-- Tabela para armazenar utilizadores
CREATE TABLE utilizadores (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(100) NOT NULL,
    password VARCHAR(255) NOT NULL,
    permissao ENUM('admin', 'user') NOT NULL
);


CREATE TABLE config_telegram (
    id INT AUTO_INCREMENT PRIMARY KEY,
    chave VARCHAR(50) NOT NULL,
    valor TEXT NOT NULL,
    id_utilizador INT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (id_utilizador) REFERENCES utilizadores(id) ON DELETE CASCADE,
    UNIQUE KEY chave_utilizador (chave, id_utilizador)
);


CREATE TABLE utilizadores_sensores (
    id_utilizador INT,
    id_sensor INT,
    PRIMARY KEY (id_utilizador, id_sensor),
    FOREIGN KEY (id_utilizador) REFERENCES utilizadores(id) ON DELETE CASCADE,
    FOREIGN KEY (id_sensor) REFERENCES sistemas_sensores(id) ON DELETE CASCADE
);


CREATE TABLE evento_sensor (
    id_evento_sensor INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    evento_sensor_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_sensor INT NOT NULL,
    tipo_alerta ENUM('temperature', 'humidity', 'CO2', 'luminosity', 'medium', 'high'),
    valor_alerta FLOAT NOT NULL,
    FOREIGN KEY (id_sensor) REFERENCES sistemas_sensores(id) 
);
INSERT INTO utilizadores (nome, password, permissao) 
VALUES ('1', '1', 'admin');

CREATE TABLE IF NOT EXISTS utilizador_imagem (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    image_path VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES utilizadores(id) ON DELETE CASCADE
);

drop table utilizador_imagem;

CREATE TABLE IF NOT EXISTS areas_utilizador (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    x FLOAT NOT NULL,
    y FLOAT NOT NULL,
    sensor_x FLOAT DEFAULT NULL,
    sensor_y FLOAT DEFAULT NULL,
    width FLOAT NOT NULL,
    height FLOAT NOT NULL,
    description VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL,
    sensor_id INT DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES utilizadores(id) ON DELETE CASCADE,
    FOREIGN KEY (sensor_id) REFERENCES sistemas_sensores(id) ON DELETE CASCADE,
    CONSTRAINT fk_areas_user_sensor FOREIGN KEY (user_id, sensor_id)
        REFERENCES utilizadores_sensores(id_utilizador, id_sensor)
        ON DELETE CASCADE
);

