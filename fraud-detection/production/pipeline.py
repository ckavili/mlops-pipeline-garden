# kfp imports
import kfp
import kfp.dsl as dsl
from kfp.dsl import (
    component,
    Input,
    Output,
    Dataset,
    Metrics,
)
from kfp.kubernetes import use_secret_as_env

# Misc imports
import os

# Component imports
from fetch_data import fetch_transactionsdb_data
from data_validation import validate_transactiondb_data
from data_preprocessing import preprocess_transactiondb_data
from train_model import train_fraud_model, convert_keras_to_onnx
from evaluate_model import evaluate_keras_model_performance, validate_onnx_model
from save_model import push_to_model_registry

######### Pipeline definition #########

data_connection_secret_name = 'aws-connection-models'

# Create pipeline
@dsl.pipeline(
  name='fraud-detection-training-pipeline',
  description='Trains the fraud detection model.'
)
def fraud_training_pipeline(datastore: dict, hyperparameters: dict, version: str):
    fetch_task = fetch_transactionsdb_data(datastore = datastore)
    data_validation_task = validate_transactiondb_data(dataset = fetch_task.outputs["dataset"])
    pre_processing_task = preprocess_transactiondb_data(in_data = fetch_task.outputs["dataset"])
    training_task = train_fraud_model(
        train_data = pre_processing_task.outputs["train_data"], 
        val_data = pre_processing_task.outputs["val_data"],
        scaler = pre_processing_task.outputs["scaler"],
        class_weights = pre_processing_task.outputs["class_weights"],
        hyperparameters = hyperparameters,
    )
    convert_task = convert_keras_to_onnx(keras_model = training_task.outputs["trained_model"])
    model_evaluation_task = evaluate_keras_model_performance(
        model = training_task.outputs["trained_model"],
        test_data = pre_processing_task.outputs["test_data"],
        scaler = pre_processing_task.outputs["scaler"],
        previous_model_metrics = {"accuracy":0.85},
    )
    model_validation_task = validate_onnx_model(
        keras_model = training_task.outputs["trained_model"],
        onnx_model = convert_task.outputs["onnx_model"],
        test_data = pre_processing_task.outputs["test_data"]
    )
    register_model_task = push_to_model_registry( version = version,
        model = convert_task.outputs["onnx_model"]
    )
    use_secret_as_env(
        register_model_task,
        secret_name=data_connection_secret_name,
        secret_key_to_env={
            'AWS_S3_ENDPOINT': 'AWS_S3_ENDPOINT',
            'AWS_ACCESS_KEY_ID': 'AWS_ACCESS_KEY_ID',
            'AWS_SECRET_ACCESS_KEY': 'AWS_SECRET_ACCESS_KEY',
            'AWS_S3_BUCKET': 'AWS_S3_BUCKET',
        },
    )

if __name__ == '__main__':
    metadata = {
        "datastore": {
            "uri": "transactionsdb.mlops-transactionsdb.svc.cluster.local",
            "table": "transactions.transactions"
        },
        "hyperparameters": {
            "epochs": 2
        }
    }
        
    namespace_file_path =\
        '/var/run/secrets/kubernetes.io/serviceaccount/namespace'
    with open(namespace_file_path, 'r') as namespace_file:
        namespace = namespace_file.read()

    kubeflow_endpoint =\
        f'https://ds-pipeline-dspa.{namespace}.svc:8443'

    sa_token_file_path = '/var/run/secrets/kubernetes.io/serviceaccount/token'
    with open(sa_token_file_path, 'r') as token_file:
        bearer_token = token_file.read()

    ssl_ca_cert =\
        '/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt'

    print(f'Connecting to Data Science Pipelines: {kubeflow_endpoint}')
    client = kfp.Client(
        host=kubeflow_endpoint,
        existing_token=bearer_token,
        ssl_ca_cert=ssl_ca_cert
    )

    client.create_run_from_pipeline_func(
        fraud_training_pipeline,
        arguments=metadata,
        experiment_name="fraud-training",
        namespace="mlops-dev-zone",
        enable_caching=True
    )
