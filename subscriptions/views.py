from core.region import get_region
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from subscriptions.models import Subscription, subscriptions_with_new_listings_count
from subscriptions.serializers import SubscriptionSerializer
from catalog.models import Book

class SubscriptionListView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        region = get_region(request)
        subs = subscriptions_with_new_listings_count(
            Subscription.objects.filter(user=request.user, region=region)
        )
        serializer = SubscriptionSerializer(subs, many=True)
        return Response(serializer.data)

    def delete(self, request):
        region = get_region(request)
        Subscription.objects.filter(user=request.user, region=region).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def post(self, request):
        book_id = request.data.get('book_id')
        if not book_id:
            return Response({"error": "book_id required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            from catalog.services import clean_and_validate_isbn
            valid_isbn = clean_and_validate_isbn(book_id)
            if valid_isbn:
                region = get_region(request)
                book = Book.objects.filter(region=region, isbn13=valid_isbn).first()
                if not book:
                    # Create it dynamically from Google Books API
                    from catalog.services import get_google_books_by_isbn
                    gb_data = get_google_books_by_isbn(valid_isbn)
                    if not gb_data:
                        return Response({"error": "Book not found in Google Books"}, status=status.HTTP_404_NOT_FOUND)
                    
                    book = Book.objects.create(
                        isbn13=valid_isbn,
                        title=gb_data['title'],
                        authors=gb_data['authors'],
                        publisher=gb_data.get('publisher', ''),
                        published_date=gb_data.get('published_date', ''),
                        cover_url=gb_data.get('cover_url', ''),
                        source='google_api',
                        region=region
                    )
            else:
                region = get_region(request)
                book = Book.objects.get(region=region, id=book_id)

            region = get_region(request)
            sub, created = Subscription.objects.get_or_create(
                user=request.user,
                book=book,
                region=region
            )
            serializer = SubscriptionSerializer(sub)
            return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        except (Book.DoesNotExist, ValueError):
            return Response({"error": "Book not found"}, status=status.HTTP_404_NOT_FOUND)

class SubscriptionDetailView(views.APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, id):
        try:
            region = get_region(request)
            sub = Subscription.objects.get(id=id, user=request.user, region=region)
            sub.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Subscription.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
