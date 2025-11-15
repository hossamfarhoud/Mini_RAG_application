from fastapi import APIRouter, Request, status, UploadFile, File
from fastapi.responses import JSONResponse
from models import AssetModel, ProjectModel, ResponseSignal
from models.enums.AssetTypeEnum import AssetTypeEnum
from controllers import ProcessController
from .schemes.data import ProcessRequest
import os
import uuid

app = APIRouter()


@app.get("/api/v1/data/welcome")
async def welcome():
    return {"message": "Welcome to Mini RAG API"}


@app.post("/api/v1/data/upload/{project_id}")
async def upload_endpoint(request: Request, project_id: str, file: UploadFile = File(...)):
    """
    Upload a file to a specific project
    """
    # Validate file type
    if file.content_type not in request.app.state.file_allowed_types:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "signal": ResponseSignal.FILE_TYPE_NOT_SUPPORTED.value,
            }
        )
    
    # Read file content
    file_content = await file.read()
    file_size = len(file_content)
    
    # Validate file size
    max_size = request.app.state.file_max_size * 1024 * 1024  # Convert MB to bytes
    if file_size > max_size:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "signal": ResponseSignal.FILE_SIZE_EXCEEDED.value,
            }
        )
    
    # Get or create project
    project_model = await ProjectModel.create_instance(
        db_client=request.app.db_client
    )
    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )
    
    # Create asset model
    asset_model = await AssetModel.create_instance(
        db_client=request.app.db_client
    )
    
    # Generate unique filename
    random_name = str(uuid.uuid4()).replace("-", "")[:12]
    file_extension = os.path.splitext(file.filename)[1]
    unique_filename = f"{random_name}_{file.filename}"
    
    # Save file to disk
    assets_dir = request.app.state.assets_dir
    file_path = os.path.join(assets_dir, unique_filename)
    
    with open(file_path, "wb") as f:
        f.write(file_content)
    
    # Create asset record in database
    from models.db_schemes import Asset
    asset = Asset(
        asset_project_id=project.id,
        asset_type=AssetTypeEnum.FILE.value,
        asset_name=unique_filename,
        asset_size=file_size
    )
    
    created_asset = await asset_model.create_asset(asset)
    
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "signal": ResponseSignal.FILE_UPLOAD_SUCCESS.value,
            "file_id": str(created_asset.id)
        }
    )


@app.post("/api/v1/data/process/{project_id}")
async def process_endpoint(request: Request, project_id: str, process_request: ProcessRequest):
    """
    Process uploaded files: chunk them and create embeddings
    
    FIXED: Now correctly handles file_id as ObjectId instead of filename
    """
    chunk_size = process_request.chunk_size
    overlap_size = process_request.overlap_size
    do_reset = process_request.do_reset
    
    # Get or create the project
    project_model = await ProjectModel.create_instance(
        db_client=request.app.db_client
    )
    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )
    
    # Create asset model instance
    asset_model = await AssetModel.create_instance(
        db_client=request.app.db_client
    )
    
    project_files_ids = {}
    
    if process_request.file_id:
        # FIXED: Use get_asset_by_id instead of get_asset_record
        # This correctly queries by _id field instead of asset_name
        asset_record = await asset_model.get_asset_by_id(
            asset_id=process_request.file_id
        )
        
        # Check if asset exists
        if asset_record is None:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "signal": ResponseSignal.FILE_ID_ERROR.value,
                }
            )
        
        # FIXED: Validate that the asset belongs to the correct project
        # This ensures users can't process files from other projects
        if str(asset_record.asset_project_id) != str(project.id):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "signal": "file_does_not_belong_to_this_project",
                }
            )
        
        project_files_ids = {
            asset_record.id: asset_record.asset_name
        }
    else:
        # Process all files in the project if no specific file_id provided
        project_files = await asset_model.get_all_project_assets(
            asset_project_id=project.id,
            asset_type=AssetTypeEnum.FILE.value,
        )
        project_files_ids = {
            record.id: record.asset_name
            for record in project_files
        }
    
    # Validate that there are files to process
    if len(project_files_ids) == 0:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "signal": ResponseSignal.NO_FILES_ERROR.value,
            }
        )
    
    # Initialize process controller
    process_controller = ProcessController(project_id=project_id)
    no_records = 0
    
    # Process each file
    for file_id, file_name in project_files_ids.items():
        file_path = os.path.join(request.app.state.assets_dir, file_name)
        
        # Check if file exists on disk
        if not os.path.exists(file_path):
            continue
        
        # Process the file
        try:
            records_count = await process_controller.process_file(
                db_client=request.app.db_client,
                file_id=file_id,
                file_path=file_path,
                chunk_size=chunk_size,
                overlap_size=overlap_size,
                do_reset=do_reset
            )
            no_records += records_count
        except Exception as e:
            print(f"Error processing file {file_name}: {e}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "signal": ResponseSignal.PROCESSING_FAILED.value,
                    "error": str(e)
                }
            )
    
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "signal": ResponseSignal.PROCESSING_SUCCESS.value,
            "no_records": no_records
        }
    )


@app.post("/api/v1/data/nlp_index_push/{project_id}")
async def nlp_index_push_endpoint(request: Request, project_id: str):
    """
    Push processed chunks to the vector database index
    """
    # Get project
    project_model = await ProjectModel.create_instance(
        db_client=request.app.db_client
    )
    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )
    
    # Initialize process controller
    process_controller = ProcessController(project_id=project_id)
    
    try:
        # Push to vector database
        result = await process_controller.push_to_vector_db(
            db_client=request.app.db_client,
            project_id=str(project.id)
        )
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "signal": ResponseSignal.INSERT_INTO_VECTORDB_SUCCESS.value,
                "indexed_count": result
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "signal": ResponseSignal.INSERT_INTO_VECTORDB_ERROR.value,
                "error": str(e)
            }
        )


@app.get("/api/v1/data/nlp_index_info/{project_id}")
async def nlp_index_info_endpoint(request: Request, project_id: str):
    """
    Get information about the vector database index for a project
    """
    # Get project
    project_model = await ProjectModel.create_instance(
        db_client=request.app.db_client
    )
    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )
    
    # Initialize process controller
    process_controller = ProcessController(project_id=project_id)
    
    try:
        info = await process_controller.get_index_info()
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "signal": ResponseSignal.VECTORDB_COLLECTION_RETRIEVED.value,
                "info": info
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "signal": "error",
                "error": str(e)
            }
        )


@app.post("/api/v1/data/nlp_index_search/{project_id}")
async def nlp_index_search_endpoint(request: Request, project_id: str):
    """
    Search the vector database index
    """
    from .schemes.data import SearchRequest
    
    body = await request.json()
    search_request = SearchRequest(**body)
    
    # Get project
    project_model = await ProjectModel.create_instance(
        db_client=request.app.db_client
    )
    project = await project_model.get_project_or_create_one(
        project_id=project_id
    )
    
    # Initialize process controller
    process_controller = ProcessController(project_id=project_id)
    
    try:
        results = await process_controller.search_index(
            query=search_request.query,
            top_k=search_request.top_k
        )
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "signal": ResponseSignal.VECTORDB_SEARCH_SUCCESS.value,
                "results": results
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "signal": ResponseSignal.VECTORDB_SEARCH_ERROR.value,
                "error": str(e)
            }
        )