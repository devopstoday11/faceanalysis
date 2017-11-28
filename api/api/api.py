import os
from flask import Flask, Request, request, redirect, url_for
from werkzeug.utils import secure_filename
from flask_restful import Resource, Api, reqparse
from .models.models import Match, FaceImage, PendingFaceImage
from .models.database_manager import DatabaseManager
import werkzeug
from azure.storage.queue import QueueService

app = Flask(__name__)

# TODO: PUT MSG ON QUEUE
# TODO: TEST MAX CONTENT LENGTH and SEE IF THESE VALUES ARE USED BESIDES IMGUPLOAD
app.config['UPLOAD_FOLDER'] = '/app/api/images/input'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

api = Api(app)

queue_service = QueueService(account_name=os.environ['STORAGE_ACCOUNT_NAME'],
                             account_key=os.environ['STORAGE_ACCOUNT_KEY'])
queue_service.create_queue(os.environ['IMAGE_PROCESSOR_QUEUE'])

class ImgUpload(Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('file', type=werkzeug.datastructures.FileStorage, location='files')
        args = parser.parse_args()
        file = args['file']
        if file and self._allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            img_id = file.filename[:-4]
            self._add_pending_face_img(img_id)
            queue_service.put_message(os.environ['IMAGE_PROCESSOR_QUEUE'], img_id)
            return {'success': True}

    def get(self, img_id):
        session = DatabaseManager().get_session()
        query = session.query(PendingFaceImage).filter(PendingFaceImage.original_img_id == img_id).all()
        session.close()
        return {'finished_processing': False} if len(query) else {'finished_processing': True}

    def _add_pending_face_img(self, img_id):
        print("adding pending face image: ", img_id)
        db = DatabaseManager()
        session = db.get_session()
        query = session.query(PendingFaceImage).filter(PendingFaceImage.original_img_id == img_id).all()
        session.close()
        if len(query) == 0:
            session = db.get_session()
            pfi = PendingFaceImage(original_img_id=img_id)
            session.add(pfi)
            db.safe_commit(session)

    def _allowed_file(self, filename):
        allowed_extensions = ['jpg']
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

class CroppedImgMatchList(Resource):
    def get(self, img_id):
        session = DatabaseManager().get_session()
        query = session.query(Match).filter(Match.cropped_img_id_1 == img_id)
        session.close()
        imgs = []
        distances = []
        for match in query:
            imgs.append(match.cropped_img_id_2)
            distances.append(match.distance_score)
        return {'imgs': imgs,
                'distances': distances}

#class OriginalImageMatchList(Resource):
#    def get(self, img_id):
#        session = DatabaseManager().get_session()
#        cropped_query = session.query(Match).filter(Match.image == img_id)
#        cropped_matches = [m.matched_image for m in cropped_query]
#        original_query = session.query(FaceImage).filter(FaceImage.cropped_image_id.in_(cropped_matches))
#        original_matches = [f.original_image_id for f in original_query]
#        return {'imgs': original_matches}

class CroppedImgListFromOriginalImgId(Resource):
    def get(self, orig_img_id):
        session = DatabaseManager().get_session()
        query = session.query(FaceImage).filter(FaceImage.original_img_id == orig_img_id)
        session.close()
        imgs = list(set(f.cropped_img_id for f in query))
        return {'imgs': imgs}

class OriginalImgList(Resource):
    def get(self):
        session = DatabaseManager().get_session()
        query = session.query(FaceImage).all()
        session.close()
        imgs = list(set(f.original_img_id for f in query))
        return {'imgs': imgs}

class OriginalImgListFromCroppedImgId(Resource):
    def get(self, crop_img_id):
        session = DatabaseManager().get_session()
        query = session.query(FaceImage).filter(FaceImage.cropped_img_id == crop_img_id).first()
        session.close()
        imgs = [query.original_img_id]
        return {'imgs': imgs}

api.add_resource(ImgUpload, '/api/upload_image/', '/api/upload_image/<string:img_id>/')
api.add_resource(CroppedImgMatchList, '/api/cropped_image_matches/<string:img_id>/')
#api.add_resource(OriginalImageMatchList, '/api/original_image_matches/<string:img_id>/')
api.add_resource(OriginalImgList, '/api/original_images/')
api.add_resource(OriginalImgListFromCroppedImgId, '/api/original_images/<string:crop_img_id>/')
api.add_resource(CroppedImgListFromOriginalImgId, '/api/cropped_images/<string:orig_img_id>/')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
