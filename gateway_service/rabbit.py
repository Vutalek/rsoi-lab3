import json
import os

import pika
import requests
from fastapi import HTTPException


def connect():
    connection = pika.BlockingConnection(pika.URLParameters(os.environ["RABBITMQ_URL"]))
    channel = connection.channel()
    channel.queue_declare(queue="ticket.pay.retry", durable=True)
    return connection, channel


def post(url, **kwargs):
    response = requests.post(url, timeout=5, **kwargs)
    if response.status_code >= 500:
        raise requests.ConnectionError("Service unavailable")
    response.raise_for_status()


def post_or_queue(url, **kwargs):
    try:
        post(url, **kwargs)
        return True
    except requests.HTTPError as error:
        raise HTTPException(error.response.status_code) from error
    except (requests.ConnectionError, requests.Timeout):
        connection, channel = connect()
        try:
            channel.confirm_delivery()
            channel.basic_publish(
                exchange="", routing_key="ticket.pay.retry", mandatory=True,
                body=json.dumps({"url": url, "kwargs": kwargs}),
                properties=pika.BasicProperties(delivery_mode=2),
            )
        finally:
            connection.close()
        return False


if __name__ == "__main__":
    connection, channel = connect()

    def consume(channel, method, properties, body):
        task = json.loads(body)
        try:
            post(task["url"], **task.get("kwargs", {}))
        except (requests.ConnectionError, requests.Timeout):
            connection.sleep(10)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        except requests.HTTPError:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        else:
            channel.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue="ticket.pay.retry", on_message_callback=consume, auto_ack=False)
    channel.start_consuming()
