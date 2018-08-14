using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Threading.Tasks;
using Microsoft.Azure.CognitiveServices.Vision.Face;
using Microsoft.Azure.CognitiveServices.Vision.Face.Models;
using RateLimiter;

namespace FaceApi
{
    public class FaceIdentifier
    {
        private static readonly TimeSpan TrainingPollInterval = TimeSpan.FromSeconds(10);
        private static readonly TimeSpan RateLimitInterval = TimeSpan.FromSeconds(1);
        private static readonly int RateLimitRequests = 10;

        private TimeLimiter RateLimit { get; }
        private FaceClient Client { get; }

        public FaceIdentifier(string apiKey, string apiEndpoint)
        {
            Client = new FaceClient(new ApiKeyServiceClientCredentials(apiKey))
            {
                Endpoint = apiEndpoint
            };

            RateLimit = TimeLimiter.GetFromMaxCountByInterval(RateLimitRequests, RateLimitInterval);
        }

        public async Task<bool> Predict(string groupId, double matchThreshold, string imagePath1, string imagePath2)
        {
            var allFaces = await Task.WhenAll(DetectFaces(imagePath1), DetectFaces(imagePath2));
            var faces1 = allFaces[0];
            var faces2 = allFaces[1];

            if (faces1.Count == 0 || faces2.Count == 0)
            {
                return false;
            }

            var allPeople = await IdentifyPeople(faces1.Concat(faces2), groupId, matchThreshold);
            var people1 = allPeople.Where(candidates => faces1.Contains(candidates.FaceId)).SelectMany(candidates => candidates.Candidates).Select(person => person.PersonId).ToHashSet();
            var people2 = allPeople.Where(candidates => faces2.Contains(candidates.FaceId)).SelectMany(candidates => candidates.Candidates).Select(person => person.PersonId).ToHashSet();

            return people1.Any(person => people2.Contains(person));
        }

        public async Task<string> Train(string trainSetRoot)
        {
            var groupId = Guid.NewGuid().ToString();

            await CreatePersonGroup(groupId);

            var names = Directory.GetDirectories(trainSetRoot).Select(Path.GetFileName);

            var people = await CreatePeople(groupId, names);

            await AddFaces(groupId, trainSetRoot, people);

            var success = await TrainPersonGroup(groupId);

            return success ? groupId : null;
        }

        private async Task<IList<IdentifyResult>> IdentifyPeople(IEnumerable<Guid> faceIds, string groupId, double matchThreshold)
        {
            return await RateLimit.Perform(async () =>
            {
                var results = await Client.Face.IdentifyAsync(
                    faceIds: faceIds.ToList(),
                    largePersonGroupId: groupId,
                    confidenceThreshold: matchThreshold);

                foreach (var result in results)
                {
                    await Console.Error.WriteLineAsync($"Matched {result.Candidates.Count} people for face {result.FaceId}");
                }

                return results;
            });
        }

        private async Task<IList<Guid>> DetectFaces(string imagePath)
        {
            using (var stream = File.OpenRead(imagePath))
            {
                return await RateLimit.Perform(async () =>
                {
                    var result = await Client.Face.DetectWithStreamAsync(stream, returnFaceId: true);
                    await Console.Error.WriteLineAsync($"Got {result.Count} faces for image {imagePath}");
                    return result.Select(face => face.FaceId.Value).ToList();
                });
            }
        }

        private async Task CreatePersonGroup(string groupId)
        {
            await RateLimit.Perform(async () =>
            {
                await Client.LargePersonGroup.CreateAsync(groupId, groupId);
                await Console.Error.WriteLineAsync($"Created person group {groupId}");
            });
        }

        private async Task<bool> TrainPersonGroup(string groupId)
        {
            await RateLimit.Perform(async () =>
            {
                await Client.LargePersonGroup.TrainAsync(groupId);
                await Console.Error.WriteLineAsync($"Trained person group {groupId}");
            });

            while (true)
            {
                var status = await Client.LargePersonGroup.GetTrainingStatusAsync(groupId);
                switch (status.Status)
                {
                    case TrainingStatusType.Nonstarted:
                    case TrainingStatusType.Running:
                        await Task.Delay(TrainingPollInterval);
                        break;

                    case TrainingStatusType.Succeeded:
                        return true;

                    case TrainingStatusType.Failed:
                        return false;
                }
            }
        }

        private async Task<IEnumerable<Person>> CreatePeople(string groupId, IEnumerable<string> names)
        {
            return await Task.WhenAll(names.Select(name =>
                RateLimit.Perform(async () =>
                {
                    var result = await Client.LargePersonGroupPerson.CreateAsync(groupId, name);
                    await Console.Error.WriteLineAsync($"Created person {name}");
                    result.Name = name;
                    return result;
                })));
        }

        private async Task<IEnumerable<PersistedFace>> AddFaces(string groupId, string trainSetRoot, IEnumerable<Person> people)
        {
            return await Task.WhenAll(people.SelectMany(person =>
                Directory.GetFiles(Path.Combine(trainSetRoot, person.Name)).Select(face =>
                    AddFace(groupId, person.PersonId, face)).Where(face => face != null)));
        }

        private async Task<PersistedFace> AddFace(string groupId, Guid personId, string facePath)
        {
            using (var stream = File.OpenRead(facePath))
            {
                return await RateLimit.Perform(async () =>
                {
                    try
                    {
                        var result = await Client.LargePersonGroupPerson.AddFaceFromStreamAsync(groupId, personId, stream);
                        await Console.Error.WriteLineAsync($"Uploaded {facePath}");
                        return result;
                    }
                    catch (Exception)
                    {
                        await Console.Error.WriteLineAsync($"Unable to upload {facePath}");
                        return null;
                    }
                });
            }
        }
    }
}
