# B2B Logistics Portal

A multi-tenant logistics management system built with Django, Django REST Framework, and Celery. This application allows companies to manage their products, process orders, and generate exports with role-based access control.

## Features

- **Multi-tenant Architecture**: Each company's data is completely isolated
- **Role-based Access Control**:
  - **Admin**: Full access to all features
  - **Operator**: Can create and manage their own orders
  - **Viewer**: Read-only access
- **Order Processing**:
  - Create and manage orders with status tracking (pending, processing, approved, failed)
  - Bulk order creation
  - Automatic stock management
- **Data Export**: Generate CSV exports of order data
- **Background Processing**: Asynchronous order processing with Celery
- **RESTful API**: Full-featured API for integration
- **Docker Support**: Easy containerized deployment

## Tech Stack

- **Backend**: Python 3.13, Django 4.x, Django REST Framework
- **Database**: PostgreSQL
- **Task Queue**: Celery with Redis
- **Web Server**: Nginx (production)
- **Containerization**: Docker, docker-compose

## Prerequisites

- Docker

## Quick Start with Docker

1. **Clone the repository**

   ```bash
   git clone git@github.com:hosamhamdy258/logistics-app.git
   cd task
   ```

2. **Build and start the services**

   ```bash
   docker compose up -d --build
   ```

3. **Run database migrations**

   ```bash
   docker compose exec web python manage.py migrate
   ```

4. **Load sample data**

   ```bash
   docker compose exec web python demo.py
   ```

    ```
    superuser 
        username: admin
        password : admin

    companies [ seaport (seaport.net) , airport (airport.net)]

    users list & password -> demo123
        demo_seaport_admin@seaport.net
        demo_seaport_operator_1@seaport.net
        demo_seaport_operator_2@seaport.net
        demo_seaport_viewer@seaport.net
        demo_airport_admin@airport.net
        demo_airport_operator_1@airport.net
        demo_airport_operator_2@airport.net
        demo_airport_viewer@airport.net

    ```

5. **Access the application**
   - Admin interface: http://localhost/admin/
   - API root: http://localhost/api/
   - API documentation: http://localhost/api/schema/

## API Documentation

### Authentication

The API uses Token Authentication. To authenticate your requests, include the token in the `Authorization` header:

```
Authorization: Token <your_token_here>
```

### Available Endpoints

#### Products

- `GET /api/products/` - List all active products for the authenticated user's company

#### Orders

- `GET /api/orders/` - List all orders for the authenticated user's company

- `POST /api/orders/` - Create a new order

  - **Request Body**:
    ```json
    {
      "product_id": 1,
      "quantity": 2
    }
    ```

- `POST /api/orders/bulk/` - Create multiple orders at once

  - **Request Body**:
    ```json
    [
      { "product_id": 1, "quantity": 2 },
      { "product_id": 2, "quantity": 1 }
    ]
    ```

- `POST /api/orders/{id}/retry/` - Retry a failed order

#### Exports

- `GET /api/exports/{id}/download/` - Download an export file

### Health Check

- `GET /api/health/` - Check the health of the application and its dependencies

### Running Tests with Docker

```bash
# Run all tests
docker compose exec web python manage.py test
```

## Notes

### Export:

- used temp file to avoid IOBytes in memory for large amount of order exports
- used queryset iterator and select_related for performance reasons

### Retry Logic:

- used signals for observe failed orders stats and check profile instance for failed counter and auto deactivate
- added manually deactivate too as admin action in orders page

### API RATE LIMIT:

- added rate limit per user : 1000/day
-

### Other Mentions:

- choices field used CharField(choices=CHOICES) instead of extending it as ForeignKey for simplicity
- used build-in domain name validator from django 5.1 instead of creating new one manually
- marked texts as translatable even if translate is not required now it's become much easier later when generating .po files
- skipped data privacy for profiles model
- critical actions like deactivate profiles should be only allowed to privileged users
- /api/export/id/download for simplicity returned the file directly to browser from server
- skipped .env file [ secret keys / database credentials ]
