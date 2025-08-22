# KernelCI Staging Web

This is a FastAPI web application for managing KernelCI staging builds. 
It provides a web interface for triggering and monitoring staging runs, managing users, and configuring the application.
Main purpose of this application to improve developers cooperation and visibility of staging runs,
without requiring ssh privileges to staging server.

## How to Run

1.  **Install dependencies:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

2.  **Run the application:**
    ```bash
    python3 main.py
    ```
    Or use the provided run script:
    ```bash
    ./run.py
    ```

3.  **Access the web interface:**
    Open your browser and go to `http://localhost:9090`.

## Default Login

*   **Username:** admin
*   **Password:** admin

It is recommended to change the admin password after the first login.
