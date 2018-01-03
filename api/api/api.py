import os
from requests import codes
from flask import Flask, Request, abort, g
from werkzeug.utils import secure_filename
from flask_restful import Resource, Api, reqparse
from .models.models import Match, OriginalImage, User
from .models.database_manager import DatabaseManager
import werkzeug
from azure.storage.queue import QueueService
from .log import get_logger
from flask_httpauth import HTTPBasicAuth

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(
                                           os.path.dirname(
                                           os.path.abspath(__file__)),
                                           'images')
app.url_map.strict_slashes = False
api = Api(app)
queue_service = QueueService(account_name=os.environ['STORAGE_ACCOUNT_NAME'],
                             account_key=os.environ['STORAGE_ACCOUNT_KEY'])
queue_service.create_queue(os.environ['IMAGE_PROCESSOR_QUEUE'])
logger = get_logger(__name__,
                    'api.log',
                    os.environ['LOGGING_LEVEL'])

auth = HTTPBasicAuth()

@auth.verify_password
def verify_password(username_or_token, password):
    user = User.verify_auth_token(username_or_token)
    session = DatabaseManager().get_session()
    if not user:
        user = session.query(User).filter(User.username == username_or_token).first()
        if not user or not user.verify_password(password):
            session.close()
            return False
    g.user = user
    session.close()
    return True

class AuthenticationToken(Resource):
    method_decorators = [auth.login_required]

    def get(self):
        token = g.user.generate_auth_token()
        return {'token': token.decode('ascii')}

class RegisterUser(Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('username',
                            required=True,
                            help='username parameter missing in post body')
        parser.add_argument('password',
                            required=True,
                            help='password parameter missing in post body')
        args = parser.parse_args()
        username = args['username']
        password = args['password']
        db = DatabaseManager()
        session = db.get_session()
        query = session.query(User).filter(User.username == username).first()
        session.close()
        if query is not None:
            return 'User already registered', codes.BAD_REQUEST
        user = User(username=username)
        user.hash_password(password)
        session = db.get_session()
        session.add(user)
        db.safe_commit(session)
        return {'username': username}, codes.CREATED

class ProcessImg(Resource):
    method_decorators = [auth.login_required]

    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('img_id',
                            required=True,
                            help="img_id missing in the post body")
        args = parser.parse_args()
        img_id = args['img_id']
        try:
            queue_service.put_message(os.environ['IMAGE_PROCESSOR_QUEUE'],
                                      img_id)
        except:
            return 'Server error', codes.INTERNAL_SERVER_ERROR
        logger.info('img successfully uploaded and id put on queue')
        return 'OK', codes.OK

    def get(self, img_id):
        logger.debug('checking if img has been processed')
        session = DatabaseManager().get_session()
        query = session.query(OriginalImage).filter(OriginalImage.img_id == img_id).first()
        session.close()
        if query is not None:
            return {'finished_processing': True}
        else:
            return {'finished_processing': False}

class ImgUpload(Resource):
    method_decorators = [auth.login_required]
    allowed_extensions = os.environ['ALLOWED_IMAGE_FILE_EXTENSIONS'].lower().split('_')

    def post(self):
        logger.debug('uploading img')
        parser = reqparse.RequestParser()
        parser.add_argument('image',
                            type=werkzeug.datastructures.FileStorage,
                            required=True,
                            help="image missing in post body",
                            location='files')
        args = parser.parse_args()
        img = args['image']
        logger.debug("FILE RECEIVED")
        logger.debug(img.filename)
        if self._allowed_file(img.filename):
            filename = secure_filename(img.filename)
            try:
                img.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            except:
                return 'Server error', codes.INTERNAL_SERVER_ERROR
            logger.info('img successfully uploaded and id put on queue')
            return 'OK', codes.OK
        else:
            error_msg = '''Image upload failed: please use one of the following 
extensions --> {}'''.format(self.allowed_extensions)
            return error_msg, codes.BAD_REQUEST

    def _allowed_file(self, filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in self.allowed_extensions

class OriginalImgMatchList(Resource):
    method_decorators = [auth.login_required]

    def get(self, img_id):
        logger.debug('getting original img match list')
        session = DatabaseManager().get_session()
        query = session.query(Match).filter(Match.this_original_img_id == img_id)
        imgs = []
        distances = []
        for match in query:
            imgs.append(match.that_original_img_id)
            distances.append(match.distance_score)
        session.close()
        return {'imgs': imgs,
                'distances': distances}

class OriginalImgList(Resource):
    method_decorators = [auth.login_required]

    def get(self):
        logger.debug('getting original img list')
        session = DatabaseManager().get_session()
        query = session.query(OriginalImage).all()
        imgs = [f.img_id for f in query]
        session.close()
        return {'imgs': imgs}

api.add_resource(ImgUpload, '/api/upload_image')
api.add_resource(ProcessImg, '/api/process_image/', '/api/process_image/<string:img_id>')
api.add_resource(OriginalImgMatchList, '/api/original_image_matches/<string:img_id>')
api.add_resource(OriginalImgList, '/api/original_images')
api.add_resource(RegisterUser, '/api/register_user')
api.add_resource(AuthenticationToken, '/api/token')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
